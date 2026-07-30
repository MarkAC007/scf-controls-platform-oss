"""Run the CDM v2 evaluator against the committed fixture corpus.

Usage:
  python scripts/cdm_eval/run_eval.py --variant baseline
  python scripts/cdm_eval/run_eval.py --variant baseline --out fixtures-local/results
  python scripts/cdm_eval/run_eval.py --variant baseline --limit-controls 50

The harness reports two metrics because top-1 precision alone hides whether the
mapper can abstain when no correct document exists. A prior 49.2% top-1 result
looked like ordinary ranking quality work, while the separate abstention metric
showed the architectural failure: hundreds of controls that should have emitted
no mapping still produced one.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from scripts.cdm_eval import ground_truth, setup_fixture, variants

HARNESS_VERSION = "1.0.0"
RECORDED_BASELINE_TOP1 = 0.492
RECORDED_BASELINE_ABSTENTION = 0.0


@dataclass(frozen=True)
class CandidateTop:
    filename: str
    ordinal: int
    score: float


@dataclass(frozen=True)
class LoadedControl:
    id: UUID
    scf_id: str
    control_name: str | None
    control_question: str | None
    domain: str
    objectives: tuple[str, ...]
    domain_name: str | None
    domain_principle: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay CDM v2 ranking over the fixture corpus and judge top-1 results."
    )
    parser.add_argument("--variant", required=True, help="Intent-gate variant name.")
    parser.add_argument(
        "--out",
        type=Path,
        default=BACKEND_ROOT / "fixtures-local" / "results",
        help="Directory for the JSON result file. Defaults to backend/fixtures-local/results.",
    )
    parser.add_argument(
        "--limit-controls",
        type=int,
        default=None,
        help="Evaluate only the first N selected controls for debugging.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def count_mappings(session: Session) -> int:
    return int(session.execute(text("SELECT count(*) FROM cdm_mappings")).scalar_one())


def git_head() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=BACKEND_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    head = result.stdout.strip()
    return head or None


def ensure_output_dir(path: Path) -> Path:
    resolved = path if path.is_absolute() else BACKEND_ROOT / path
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def load_documents(
    session: Session, org_id: UUID
) -> tuple[list[variants.DocumentContext], dict[UUID, str]]:
    document_rows = session.execute(
        text(
            """
            SELECT id, original_filename, word_count
            FROM cdm_documents
            WHERE organization_id = :org
              AND original_filename LIKE 'realpolicy::%'
            """
        ),
        {"org": org_id},
    ).mappings().all()

    document_ids = [row["id"] for row in document_rows]
    chunk_rows: list[Any]
    if document_ids:
        chunk_rows = session.execute(
            text(
                """
                SELECT cdm_document_id, ordinal, heading, body
                FROM cdm_document_chunks
                WHERE cdm_document_id IN :document_ids
                ORDER BY cdm_document_id, ordinal
                """
            ).bindparams(bindparam("document_ids", expanding=True)),
            {"document_ids": document_ids},
        ).mappings().all()
    else:
        chunk_rows = []

    headings_by_doc: dict[UUID, list[str]] = defaultdict(list)
    first_body_by_doc: dict[UUID, str] = {}
    for row in chunk_rows:
        document_id = row["cdm_document_id"]
        heading = row["heading"]
        if heading is not None:
            headings_by_doc[document_id].append(str(heading))
        if int(row["ordinal"]) == 0 and document_id not in first_body_by_doc:
            first_body_by_doc[document_id] = str(row["body"] or "")

    documents: list[variants.DocumentContext] = []
    filenames_by_id: dict[UUID, str] = {}
    for row in sorted(document_rows, key=lambda item: setup_fixture.strip_tag(item["original_filename"])):
        document_id = row["id"]
        filename = setup_fixture.strip_tag(str(row["original_filename"]))
        filenames_by_id[document_id] = filename
        documents.append(
            variants.DocumentContext(
                cdm_document_id=document_id,
                filename=filename,
                word_count=int(row["word_count"] or 0),
                headings=tuple(headings_by_doc.get(document_id, ())),
                first_chunk_body=first_body_by_doc.get(document_id, ""),
            )
        )
    return documents, filenames_by_id


def load_objectives(session: Session) -> dict[str, tuple[str, ...]]:
    rows = session.execute(
        text(
            """
            SELECT scf_id, objective_text
            FROM scf_catalog_assessment_objectives
            ORDER BY scf_id, ao_id
            """
        )
    ).mappings().all()
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        scf_id = row["scf_id"]
        objective_text = row["objective_text"]
        if scf_id is not None and objective_text is not None:
            grouped[str(scf_id)].append(str(objective_text))
    return {scf_id: tuple(objectives) for scf_id, objectives in grouped.items()}


def load_domains(session: Session) -> dict[str, tuple[str | None, str | None]]:
    rows = session.execute(
        text(
            """
            SELECT identifier, name, principle
            FROM scf_catalog_domains
            """
        )
    ).mappings().all()
    domains: dict[str, tuple[str | None, str | None]] = {}
    for row in rows:
        identifier = row["identifier"]
        if identifier is not None:
            domains[str(identifier)] = (
                str(row["name"]) if row["name"] is not None else None,
                str(row["principle"]) if row["principle"] is not None else None,
            )
    return domains


def load_controls(
    session: Session,
    org_id: UUID,
    objectives_by_scf_id: dict[str, tuple[str, ...]],
    domains_by_identifier: dict[str, tuple[str | None, str | None]],
) -> tuple[list[LoadedControl], int]:
    """Selected controls plus the count that failed to join the catalogue.

    The outer join leaves ``scf_id`` NULL for a scoped control with no
    catalogue row. Those cannot be judged, but they are counted and reported so
    the control totals reconcile against the org's selected-control count —
    dropping them silently would make a shrinking catalogue look like a
    stable one.
    """
    from catalog_models import SCFCatalogControl
    from models import ScopedControl

    rows = session.execute(
        select(
            ScopedControl.id,
            SCFCatalogControl.scf_id,
            SCFCatalogControl.control_name,
            SCFCatalogControl.control_question,
        )
        .outerjoin(SCFCatalogControl, ScopedControl.scf_id == SCFCatalogControl.scf_id)
        .where(ScopedControl.organization_id == org_id, ScopedControl.selected.is_(True))
    ).all()

    controls: list[LoadedControl] = []
    uncatalogued = 0
    for row in rows:
        if row.scf_id is None:
            uncatalogued += 1
            continue
        scf_id = str(row.scf_id)
        domain = scf_id.split("-")[0]
        domain_name, domain_principle = domains_by_identifier.get(domain, (None, None))
        controls.append(
            LoadedControl(
                id=row.id,
                scf_id=scf_id,
                control_name=str(row.control_name) if row.control_name is not None else None,
                control_question=str(row.control_question)
                if row.control_question is not None
                else None,
                domain=domain,
                objectives=objectives_by_scf_id.get(scf_id, ()),
                domain_name=domain_name,
                domain_principle=domain_principle,
            )
        )
    return sorted(controls, key=lambda control: (control.scf_id, str(control.id))), uncatalogued


def nearest_rank(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        raise ValueError("nearest_rank requires at least one value")
    rank = max(1, math.ceil((percentile / 100.0) * len(sorted_values)))
    return sorted_values[rank - 1]


def score_distribution(scores: list[float]) -> dict[str, float | None]:
    if not scores:
        return {"min": None, "p50": None, "p90": None, "p99": None, "max": None}
    sorted_scores = sorted(scores)
    return {
        "min": round(sorted_scores[0], 6),
        "p50": round(nearest_rank(sorted_scores, 50), 6),
        "p90": round(nearest_rank(sorted_scores, 90), 6),
        "p99": round(nearest_rank(sorted_scores, 99), 6),
        "max": round(sorted_scores[-1], 6),
    }


def top_candidate(
    session: Session,
    org_id: UUID,
    backend: Any,
    query: Any,
    objectives: tuple[str, ...],
    allowed_documents: set[UUID] | None,
    filenames_by_id: dict[UUID, str],
    weights: Any,
    threshold: float,
    top_k: int,
) -> CandidateTop | None:
    from services.cdm_retrieval import compute_objective_coverage, compute_term_overlap
    from services.cdm_scoring import compose_score

    if allowed_documents == set():
        return None

    rows, _total_candidates = backend.search(session, org_id, query, limit=top_k)
    control_terms = query.all_terms()
    candidates: list[CandidateTop] = []
    for row in rows:
        if allowed_documents is not None and row.cdm_document_id not in allowed_documents:
            continue
        filename = filenames_by_id.get(row.cdm_document_id)
        if filename is None:
            continue
        coverage = compute_objective_coverage(row.matched_objectives, objectives)
        overlap = compute_term_overlap(row.body_norm, control_terms)
        components = compose_score(
            ts_rank=row.ts_rank,
            objective_coverage=coverage,
            term_overlap=overlap,
            weights=weights,
        )
        if components.score < threshold:
            continue
        candidates.append(
            CandidateTop(filename=filename, ordinal=int(row.ordinal), score=components.score)
        )
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda candidate: variants.rank_key(
            candidate.score, candidate.filename, candidate.ordinal
        ),
    )


def empty_outcome_counts() -> dict[str, int]:
    return {outcome: 0 for outcome in sorted(ground_truth.OUTCOMES)}


def build_metrics(
    outcome_counts: dict[str, int],
    covered_denominator: int,
    abstain_denominator: int,
    scores: list[float],
) -> dict[str, Any]:
    metric_a = (
        outcome_counts[ground_truth.OUTCOME_CORRECT] / covered_denominator
        if covered_denominator
        else None
    )
    metric_b = (
        outcome_counts[ground_truth.OUTCOME_CORRECT_ABSTAIN] / abstain_denominator
        if abstain_denominator
        else None
    )
    return {
        "metric_a_top1_precision": round(metric_a, 6) if metric_a is not None else None,
        "metric_b_abstention": round(metric_b, 6) if metric_b is not None else None,
        "score_distribution": score_distribution(scores),
        "outcomes": {key: outcome_counts.get(key, 0) for key in sorted(ground_truth.OUTCOMES)},
    }


def build_per_domain(
    per_domain_counts: dict[str, dict[str, int]]
) -> list[dict[str, Any]]:
    covered = ground_truth.covered_domains()
    abstain = ground_truth.abstain_domains()
    rows: list[dict[str, Any]] = []
    for domain in sorted(per_domain_counts):
        counts = per_domain_counts[domain]
        n_controls = sum(counts.values())
        is_covered = domain in covered
        is_abstain = domain in abstain
        rate: float | None
        if is_covered:
            rate = counts.get(ground_truth.OUTCOME_CORRECT, 0) / n_controls if n_controls else None
        elif is_abstain:
            rate = (
                counts.get(ground_truth.OUTCOME_CORRECT_ABSTAIN, 0) / n_controls
                if n_controls
                else None
            )
        else:
            rate = None
        row: dict[str, Any] = {
            "domain": domain,
            "n_controls": n_controls,
            "covered": is_covered,
            "abstain": is_abstain,
            "rate": round(rate, 6) if rate is not None else None,
        }
        row.update({outcome: counts.get(outcome, 0) for outcome in sorted(ground_truth.OUTCOMES)})
        rows.append(row)
    return rows


def format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def print_summary(
    result: dict[str, Any],
    retrieval_errors: list[str],
) -> None:
    metrics = result["metrics"]
    print(f"CDM eval variant: {result['variant']}")
    print(f"Output: {result['output_path']}")
    print(f"Partial run: {str(result['partial_run']).lower()}")
    print()
    print("Per-domain results:")
    print("domain  n   bucket    rate    correct  wrong  abstain_ok  abstain_missed  unexpected_abs")
    for row in result["per_domain"]:
        bucket = "covered" if row["covered"] else "abstain"
        print(
            f"{row['domain']:<6} {row['n_controls']:>3} {bucket:<8} "
            f"{format_percent(row['rate']):>7} "
            f"{row.get(ground_truth.OUTCOME_CORRECT, 0):>7} "
            f"{row.get(ground_truth.OUTCOME_WRONG, 0):>5} "
            f"{row.get(ground_truth.OUTCOME_CORRECT_ABSTAIN, 0):>10} "
            f"{row.get(ground_truth.OUTCOME_MISSED_ABSTAIN, 0):>15} "
            f"{row.get(ground_truth.OUTCOME_UNEXPECTED_ABSTAIN, 0):>14}"
        )
    print()
    metric_a = metrics["metric_a_top1_precision"]
    metric_b = metrics["metric_b_abstention"]
    delta_a = None if metric_a is None else metric_a - RECORDED_BASELINE_TOP1
    delta_b = None if metric_b is None else metric_b - RECORDED_BASELINE_ABSTENTION
    print(
        "Metric A top-1 precision: "
        f"{format_percent(metric_a)} "
        f"(delta vs recorded 49.2%: {format_percent(delta_a) if delta_a is not None else 'n/a'})"
    )
    print(
        "Metric B correct abstention: "
        f"{format_percent(metric_b)} "
        f"(delta vs recorded 0.0%: {format_percent(delta_b) if delta_b is not None else 'n/a'})"
    )
    print(
        "Recorded 49.2% top-1 is tie-affected; an unspecified tie-break produced "
        "48.5% to 49.2%, so sub-point differences are noise."
    )
    skipped = result["skipped"]
    print(
        f"Skipped unsearchable: {skipped['unsearchable']}; "
        f"unknown domain: {skipped['unknown_domain']}; "
        f"uncatalogued: {skipped['uncatalogued']}"
    )
    if retrieval_errors:
        print(f"Retrieval errors: {len(retrieval_errors)}")


def run_evaluation(args: argparse.Namespace) -> int:
    from services.cdm_retrieval import ControlQuery, PostgresFTSBackend
    from services.cdm_scoring import ScoreWeights, get_score_threshold, get_top_k

    started_at = utc_now()
    try:
        gate = variants.get_variant(args.variant)
    except KeyError as exc:
        print(f"Unknown variant: {exc}", file=sys.stderr)
        return 2

    if args.limit_controls is not None and args.limit_controls < 0:
        print("--limit-controls must be non-negative", file=sys.stderr)
        return 2

    output_dir = ensure_output_dir(args.out)
    weights = ScoreWeights.from_env()
    threshold = get_score_threshold()
    top_k = get_top_k()
    session = setup_fixture.open_session()
    before_count: int | None = None
    after_count: int | None = None
    try:
        org_id = setup_fixture.resolve_org_id(session)
        status = setup_fixture.verify_fixture(session, org_id)
        if not status.ok:
            for failure in status.failures:
                print(f"Fixture failure: {failure}", file=sys.stderr)
            return 1

        before_count = count_mappings(session)
        documents, filenames_by_id = load_documents(session, org_id)
        objectives_by_scf_id = load_objectives(session)
        domains_by_identifier = load_domains(session)
        controls, skipped_uncatalogued = load_controls(
            session, org_id, objectives_by_scf_id, domains_by_identifier
        )
        if args.limit_controls is not None:
            controls = controls[: args.limit_controls]

        backend = PostgresFTSBackend()
        per_control: list[dict[str, Any]] = []
        per_domain_counts: dict[str, dict[str, int]] = defaultdict(empty_outcome_counts)
        outcome_counts = empty_outcome_counts()
        top_scores: list[float] = []
        skipped_unsearchable = 0
        skipped_unknown_domain = 0
        retrieval_errors: list[str] = []

        for control in controls:
            if control.domain not in ground_truth.GROUND_TRUTH:
                skipped_unknown_domain += 1
                continue

            query = ControlQuery(
                scf_id=control.scf_id,
                control_name=control.control_name,
                control_question=control.control_question,
                objectives=control.objectives,
            )
            if not query.query_texts():
                skipped_unsearchable += 1
                continue

            control_context = variants.ControlContext(
                scf_id=control.scf_id,
                domain=control.domain,
                control_name=control.control_name,
                control_question=control.control_question,
                objectives=control.objectives,
                domain_name=control.domain_name,
                domain_principle=control.domain_principle,
            )
            try:
                allowed_documents = gate.allowed_documents(control_context, documents)
                candidate = top_candidate(
                    session=session,
                    org_id=org_id,
                    backend=backend,
                    query=query,
                    objectives=control.objectives,
                    allowed_documents=allowed_documents,
                    filenames_by_id=filenames_by_id,
                    weights=weights,
                    threshold=threshold,
                    top_k=top_k,
                )
            except Exception as exc:
                retrieval_errors.append(f"{control.scf_id}:{control.id}:{type(exc).__name__}: {exc}")
                continue

            top_doc = candidate.filename if candidate is not None else None
            top_score = round(candidate.score, 6) if candidate is not None else None
            if candidate is not None:
                top_scores.append(candidate.score)
            outcome = ground_truth.judge(control.domain, top_doc)
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            per_domain_counts[control.domain][outcome] = (
                per_domain_counts[control.domain].get(outcome, 0) + 1
            )
            per_control.append(
                {
                    "scf_id": control.scf_id,
                    "outcome": outcome,
                    "top_doc": top_doc,
                    "top_score": top_score,
                }
            )

        after_count = count_mappings(session)
        if before_count != after_count:
            print(
                "Read-only check failed: cdm_mappings count changed "
                f"from {before_count} to {after_count}",
                file=sys.stderr,
            )
            return 1

        covered_denominator = sum(
            sum(counts.values())
            for domain, counts in per_domain_counts.items()
            if domain in ground_truth.covered_domains()
        )
        abstain_denominator = sum(
            sum(counts.values())
            for domain, counts in per_domain_counts.items()
            if domain in ground_truth.abstain_domains()
        )
        finished_at = utc_now()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = output_dir / f"eval_{args.variant}_{stamp}.json"
        result: dict[str, Any] = {
            "harness_version": HARNESS_VERSION,
            "variant": args.variant,
            "git_head": git_head(),
            "config": {
                "threshold": threshold,
                "top_k": top_k,
                "weights": weights.as_dict(),
            },
            "corpus_fingerprint": status.fingerprint,
            "n_documents": status.n_documents,
            "n_chunks": status.n_chunks,
            "n_controls": len(per_control),
            "partial_run": args.limit_controls is not None,
            "metrics": build_metrics(
                outcome_counts=outcome_counts,
                covered_denominator=covered_denominator,
                abstain_denominator=abstain_denominator,
                scores=top_scores,
            ),
            "per_domain": build_per_domain(per_domain_counts),
            "per_control": sorted(per_control, key=lambda row: row["scf_id"]),
            "skipped": {
                "unsearchable": skipped_unsearchable,
                "unknown_domain": skipped_unknown_domain,
                "uncatalogued": skipped_uncatalogued,
            },
            "retrieval_errors": retrieval_errors,
            "started_at": started_at,
            "finished_at": finished_at,
        }
        result["output_path"] = str(output_path)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")

        print_summary(result, retrieval_errors=retrieval_errors)
        return 1 if retrieval_errors else 0
    finally:
        if before_count is not None and after_count is None:
            current_count = count_mappings(session)
            if before_count != current_count:
                print(
                    "Read-only check failed: cdm_mappings count changed "
                    f"from {before_count} to {current_count}",
                    file=sys.stderr,
                )
        session.close()


def main() -> int:
    return run_evaluation(parse_args())


if __name__ == "__main__":
    sys.exit(main())
