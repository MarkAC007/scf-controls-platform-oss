"""Provider-agnostic CDM evaluation variants.

The DB runner that arrives later should be boring glue: load controls and
documents, ask a variant which documents remain eligible, rank what is left,
and pass the selected filename to the ground-truth judge. Keeping that seam
pure Python makes three things testable before any database work exists: the
baseline behaviour, where ranking sees the whole corpus, the abstention
contract, where a gate can state that no document should be mapped, and hosted
model intent gates whose expensive calls were cached before evaluation starts.

The two protocols are the only intended extension points. A classifier or gate
can use an LLM, a self-hosted model, heuristics, or a static map, but callers
should only care about these interfaces and the registered variant name.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence
from uuid import UUID

from .ground_truth import expected_substring


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_LOCAL = _BACKEND_ROOT / "fixtures-local"


def _intents_path(provider: str) -> Path:
    return _FIXTURES_LOCAL / f"intents_{provider}.json"


def _load_json_object(path: Path, description: str) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing {description} at {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to read {description} at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {description} at {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError(f"Malformed {description} at {path}: expected a JSON object")
    return raw


def _manifest_fingerprint() -> str:
    manifest_path = _FIXTURES_LOCAL / "manifest.json"
    manifest = _load_json_object(manifest_path, "CDM fixture manifest")
    documents = manifest.get("documents")
    if not isinstance(documents, list):
        raise RuntimeError(f"Malformed CDM fixture manifest at {manifest_path}: expected a documents list")

    sha256s: list[str] = []
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise RuntimeError(
                f"Malformed CDM fixture manifest at {manifest_path}: documents[{index}] must be an object"
            )
        sha256 = document.get("sha256")
        if not isinstance(sha256, str) or not sha256:
            raise RuntimeError(
                f"Malformed CDM fixture manifest at {manifest_path}: documents[{index}].sha256 must be a non-empty string"
            )
        sha256s.append(sha256)

    # This intentionally duplicates setup_fixture.corpus_fingerprint rather than
    # importing it: variants.py is the DB-free seam, and setup_fixture pulls in
    # SQLAlchemy and fixture lifecycle plumbing.
    joined = "\n".join(sorted(sha256s))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ControlContext:
    scf_id: str
    domain: str
    control_name: str | None
    control_question: str | None
    objectives: tuple[str, ...]
    domain_name: str | None = None
    domain_principle: str | None = None


@dataclass(frozen=True)
class DocumentContext:
    cdm_document_id: UUID
    filename: str
    word_count: int
    headings: tuple[str, ...]
    first_chunk_body: str


class IntentGate(Protocol):
    """Restricts candidate documents for one control.

    ``None`` means no filtering, so every document is allowed. An empty set
    means abstain, because the gate asserts no document in this corpus
    addresses this control. A non-empty set restricts candidates to those
    document IDs. The distinction between ``None`` and ``set()`` is the whole
    mechanism by which metric B can move.
    """

    name: str

    def allowed_documents(self, control: ControlContext, documents: Sequence[DocumentContext]) -> set[UUID] | None:
        """Return the candidate document IDs allowed for this control."""


class IntentClassifier(Protocol):
    """Classifies a document into intent labels.

    Model choice is configuration, not architecture: a provider is swapped by
    registering a different implementation, with no change to calling code. No
    in-process implementation exists, because hosted-model classification runs
    ahead of evaluation in ``classify_intents.py`` and reaches the harness as a
    cache rather than a live call. No registered variant calls this protocol.
    """

    name: str

    def classify(self, document: DocumentContext) -> list[str]:
        """Return intent labels inferred for a document."""


class BaselineGate:
    """CDM v2 behaviour: every document is a candidate and ranking alone decides.

    This is the control arm every other variant is measured against.
    """

    name = "baseline"

    def allowed_documents(self, control: ControlContext, documents: Sequence[DocumentContext]) -> set[UUID] | None:
        return None


class SmokeTitleGate:
    """Harness self-test, not a design proposal.

    This gate is circular with the ground truth: it reads the answer key and
    matches document filenames against the expected substring. It exists solely
    to prove both metrics can move off their baseline, so a change of 0.0 in a
    real variant can be distinguished from a harness that cannot measure
    anything. Anyone reading a smoke result as evidence of retrieval quality has
    misread it.
    """

    name = "smoke"

    def allowed_documents(self, control: ControlContext, documents: Sequence[DocumentContext]) -> set[UUID] | None:
        substring = expected_substring(control.domain)
        if substring is None:
            return set()

        expected = substring.casefold()
        return {
            document.cdm_document_id
            for document in documents
            if expected in document.filename.casefold()
        }


class CachedIntentGate:
    """Hosted-model intent gate backed by a precomputed cache.

    The cache is built from document text before evaluation, so this gate does
    not call a model inside the per-control loop. It never consults the answer
    key; doing so would make the experiment circular in the same way the smoke
    gate is circular by design.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.name = f"intent-{provider}"
        # The registry is built at import time. Construction must not touch the
        # filesystem, or baseline runs would require intent caches they do not use.
        self._cache: dict[str, frozenset[str]] | None = None

    def _rerun_command(self, force: bool = False) -> str:
        command = f"python scripts/cdm_eval/classify_intents.py --provider {self.provider}"
        if force:
            command = f"{command} --force"
        return command

    def _load_cache(self) -> dict[str, frozenset[str]]:
        path = _intents_path(self.provider)
        try:
            cache = _load_json_object(path, "CDM intent cache")
        except RuntimeError as exc:
            if path.exists():
                raise
            raise RuntimeError(
                f"Missing CDM intent cache at {path}. Produce it with: {self._rerun_command()}"
            ) from exc

        corpus_fingerprint = cache.get("corpus_fingerprint")
        if not isinstance(corpus_fingerprint, str):
            raise RuntimeError(f"Malformed CDM intent cache at {path}: corpus_fingerprint must be a string")

        documents = cache.get("documents")
        if not isinstance(documents, dict):
            raise RuntimeError(f"Malformed CDM intent cache at {path}: documents must be an object")

        manifest_fingerprint = _manifest_fingerprint()
        if corpus_fingerprint != manifest_fingerprint:
            raise RuntimeError(
                f"CDM intent cache at {path} has corpus_fingerprint {corpus_fingerprint!r}, "
                f"but current manifest fingerprint is {manifest_fingerprint!r}. "
                f"Re-run {self._rerun_command(force=True)}."
            )

        loaded: dict[str, frozenset[str]] = {}
        for filename, entry in documents.items():
            if not isinstance(filename, str):
                raise RuntimeError(f"Malformed CDM intent cache at {path}: document keys must be strings")
            if not isinstance(entry, dict):
                raise RuntimeError(f"Malformed CDM intent cache at {path}: {filename!r} must map to an object")
            primary_domains = entry.get("primary_domains")
            if not isinstance(primary_domains, list) or not all(
                isinstance(domain, str) for domain in primary_domains
            ):
                raise RuntimeError(
                    f"Malformed CDM intent cache at {path}: {filename!r}.primary_domains must be a list of strings"
                )
            loaded[filename] = frozenset(primary_domains)

        return loaded

    def allowed_documents(self, control: ControlContext, documents: Sequence[DocumentContext]) -> set[UUID]:
        if self._cache is None:
            self._cache = self._load_cache()

        allowed: set[UUID] = set()
        for document in documents:
            domains = self._cache.get(document.filename)
            if domains is None:
                raise RuntimeError(
                    f"Document {document.filename!r} is absent from the {self.name} cache. "
                    f"Re-run {self._rerun_command(force=True)}."
                )
            if control.domain in domains:
                allowed.add(document.cdm_document_id)
        return allowed


VARIANTS: dict[str, IntentGate] = {
    "baseline": BaselineGate(),
    "intent-claude": CachedIntentGate("claude"),
    "intent-gemini": CachedIntentGate("gemini"),
    "intent-gpt": CachedIntentGate("gpt"),
    "smoke": SmokeTitleGate(),
}


def get_variant(name: str) -> IntentGate:
    try:
        return VARIANTS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown CDM eval variant {name!r}; known variants: {', '.join(sorted(VARIANTS))}") from exc


def rank_key(score: float, filename: str, ordinal: int) -> tuple[float, str, int]:
    """Sort key selecting the top-1 candidate: score DESC, filename ASC, ordinal ASC.

    The tie-break is specified because FTS ranks tie frequently across a
    12-document corpus. An unspecified tie-break makes the run
    non-reproducible, which would silently destroy the harness's only real
    guarantee: identical corpus and config produce identical metrics.
    """

    return (-score, filename, ordinal)
