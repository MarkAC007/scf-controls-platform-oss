"""Classify fixture policy intent once per hosted-model provider.

The intent cache exists because the evaluator must stay a read-only measurement
loop. Hosted-model classification is deliberately moved to this host-side
precomputation step so a later gate can consume a fixed JSON artefact rather
than making fresh model calls while ranking every control.

The prompt itself lives in ``services.cdm_intent_prompt`` so the runtime
classifier and this measurement loop cannot drift apart. ``PROMPT_VERSION``,
``MAX_DOCUMENT_CHARS`` and ``build_prompt`` are re-exported here unchanged for
the callers that already import them from this module.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.cdm_eval import setup_fixture
from services.cdm_intent_prompt import (  # noqa: F401  (re-exported for callers)
    MAX_DOCUMENT_CHARS,
    PROMPT_VERSION,
    build_prompt,
)

PROVIDER_TIMEOUT_SECONDS = 300
PSQL_TIMEOUT_SECONDS = 120
VERSION_TIMEOUT_SECONDS = 10
EXPECTED_DOMAIN_COUNT = 33
US = "\x1f"
RS = "\x1e"


@dataclass(frozen=True)
class ProviderResult:
    text: str
    model: str


@dataclass(frozen=True)
class DocumentRow:
    document_id: str
    filename: str


@dataclass(frozen=True)
class Classification:
    primary_domains: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class ClassifiedDocument:
    filename: str
    classification: Classification
    truncated: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify CDM fixture document intent once per provider."
    )
    parser.add_argument(
        "--provider",
        required=True,
        choices=sorted(_PROVIDERS),
        help="Hosted-model provider to use.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing provider intent cache.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _truncate(value: str, limit: int = 1_500) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"


def _run_subprocess(
    argv: list[str],
    label: str,
    timeout_seconds: int,
    stdin: str | None = None,
    require_stdout: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{label} timed out after {timeout_seconds}s") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed with exit code {result.returncode}; "
            f"stderr={_truncate(result.stderr.strip())!r}"
        )
    if require_stdout and not result.stdout.strip():
        raise RuntimeError(f"{label} produced empty stdout")
    return result


def _run_psql(label: str, sql: str) -> str:
    # Explicit field and record separators prevent embedded newlines in SCF
    # principles or document bodies from corrupting row parsing.
    argv = [
        "docker",
        "exec",
        "cg-scf-postgres",
        "psql",
        "-U",
        "cg",
        "-d",
        "cg_scf",
        "-tA",
        "-F",
        US,
        "-R",
        RS,
        "-c",
        sql,
    ]
    return _run_subprocess(argv, label, PSQL_TIMEOUT_SECONDS, require_stdout=False).stdout


def _psql_records(output: str) -> list[str]:
    # psql -R separates records with RS but still terminates its output with a
    # single newline (observed: `SELECT 1 UNION ALL SELECT 2` -> b"1\x1e2\n"),
    # which would otherwise glue onto the final record's last field.
    if output.endswith("\n"):
        output = output[:-1]
    if output.endswith(RS):
        output = output[:-len(RS)]
    if output == "":
        return []
    return output.split(RS)


def load_domains() -> dict[str, tuple[str, str]]:
    output = _run_psql(
        "load SCF domains",
        "SELECT identifier, name, principle FROM scf_catalog_domains ORDER BY identifier",
    )
    domains: dict[str, tuple[str, str]] = {}
    for index, record in enumerate(_psql_records(output), start=1):
        fields = record.split(US)
        if len(fields) != 3:
            raise RuntimeError(f"Domain query row #{index} had {len(fields)} fields; expected 3")
        identifier, name, principle = fields
        if not identifier.strip():
            raise RuntimeError(f"Domain query row #{index} has an empty identifier")
        if identifier in domains:
            raise RuntimeError(f"Domain query returned duplicate identifier {identifier!r}")
        domains[identifier] = (name, principle)

    if len(domains) != EXPECTED_DOMAIN_COUNT:
        raise RuntimeError(
            f"Domain query returned {len(domains)} domains; expected {EXPECTED_DOMAIN_COUNT}"
        )
    return domains


def load_document_rows(manifest_filenames: set[str]) -> dict[str, DocumentRow]:
    output = _run_psql(
        "load fixture document ids",
        "SELECT id, original_filename FROM cdm_documents "
        f"WHERE original_filename LIKE '{setup_fixture.TAG}%' ORDER BY original_filename",
    )
    documents: dict[str, DocumentRow] = {}
    for index, record in enumerate(_psql_records(output), start=1):
        fields = record.split(US)
        if len(fields) != 2:
            raise RuntimeError(f"Document query row #{index} had {len(fields)} fields; expected 2")
        raw_id, original_filename = fields
        try:
            parsed_uuid = uuid.UUID(raw_id)
        except ValueError as exc:
            raise RuntimeError(f"Document query row #{index} has invalid UUID {raw_id!r}") from exc
        filename = setup_fixture.strip_tag(original_filename)
        if filename in documents:
            raise RuntimeError(f"Document query returned duplicate fixture filename {filename!r}")
        documents[filename] = DocumentRow(document_id=str(parsed_uuid), filename=filename)

    db_filenames = set(documents)
    if db_filenames != manifest_filenames:
        missing = sorted(manifest_filenames - db_filenames)
        extra = sorted(db_filenames - manifest_filenames)
        print("Missing fixture documents in database:", file=sys.stderr)
        for filename in missing:
            print(f"  - {filename}", file=sys.stderr)
        if not missing:
            print("  - none", file=sys.stderr)
        print("Extra fixture documents in database:", file=sys.stderr)
        for filename in extra:
            print(f"  - {filename}", file=sys.stderr)
        if not extra:
            print("  - none", file=sys.stderr)
        raise RuntimeError("Database fixture documents do not match the manifest exactly")

    if len(documents) != setup_fixture.EXPECTED_DOCUMENT_COUNT:
        raise RuntimeError(
            f"Document query returned {len(documents)} documents; "
            f"expected {setup_fixture.EXPECTED_DOCUMENT_COUNT}"
        )
    return documents


def load_document_text(document: DocumentRow) -> str:
    try:
        parsed_uuid = uuid.UUID(document.document_id)
    except ValueError as exc:
        raise RuntimeError(f"{document.filename}: invalid document UUID {document.document_id!r}") from exc

    output = _run_psql(
        f"load document text for {document.filename}",
        "SELECT string_agg(body, E'\\n' ORDER BY ordinal) "
        "FROM cdm_document_chunks "
        f"WHERE cdm_document_id = '{parsed_uuid}'",
    )
    records = _psql_records(output)
    if len(records) != 1:
        raise RuntimeError(f"{document.filename}: text query returned {len(records)} rows; expected 1")
    text = records[0]
    if not text.strip():
        raise RuntimeError(f"{document.filename}: document text is empty")
    return text


def run_provider(provider: str, prompt: str) -> ProviderResult:
    try:
        handler = _PROVIDERS[provider]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported provider {provider!r}") from exc
    return handler(prompt)


def _probe_version(argv: list[str], fallback: str) -> str:
    try:
        result = _run_subprocess(
            argv,
            "probe provider version",
            VERSION_TIMEOUT_SECONDS,
            require_stdout=True,
        )
    except Exception:
        # A missing version string only degrades the recorded model id, so it must
        # not abort a classification run that is otherwise proceeding normally.
        return fallback
    version = result.stdout.strip()
    return version or fallback


def _run_claude(prompt: str) -> ProviderResult:
    result = _run_subprocess(
        ["claude", "-p", "--output-format", "json", "--max-turns", "1"],
        "claude classification",
        PROVIDER_TIMEOUT_SECONDS,
        stdin=prompt,
    )
    try:
        raw: object = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude returned invalid JSON envelope: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("claude returned a non-object JSON envelope")

    model_text = raw.get("result")
    if not isinstance(model_text, str) or not model_text.strip():
        raise RuntimeError("claude JSON envelope missing non-empty string result")

    model = raw.get("model")
    if isinstance(model, str) and model.strip():
        model_id = model.strip()
    else:
        model_usage = raw.get("modelUsage")
        model_id = ""
        if isinstance(model_usage, dict) and len(model_usage) == 1:
            model_key = next(iter(model_usage))
            if isinstance(model_key, str):
                model_id = model_key.strip()
        if not model_id:
            model_id = _probe_version(["claude", "--version"], "claude")
    return ProviderResult(text=model_text, model=model_id)


GPT_MODEL = "gpt-5.5"


def _run_gpt(prompt: str) -> ProviderResult:
    # Direct API call rather than the codex CLI: the codex account is
    # quota-blocked until 2026-08-28, and the provider seam exists precisely so
    # transport can change without touching the measurement contract. The call
    # is read-only with respect to the corpus — it sends text out and receives
    # a classification back.
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; the gpt provider needs it")

    import urllib.error
    import urllib.request

    payload = json.dumps(
        {
            "model": GPT_MODEL,
            "reasoning_effort": "high",
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=PROVIDER_TIMEOUT_SECONDS) as response:
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = _truncate(exc.read().decode("utf-8", errors="replace"))
        raise RuntimeError(f"gpt API call failed with HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"gpt API call failed: {exc}") from exc

    try:
        raw: object = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gpt API returned invalid JSON envelope: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("gpt API returned a non-object JSON envelope")

    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("gpt API envelope has no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("gpt API envelope missing non-empty message content")

    model = raw.get("model")
    model_id = model.strip() if isinstance(model, str) and model.strip() else GPT_MODEL
    return ProviderResult(text=content, model=model_id)


_PROVIDERS: dict[str, Callable[[str], ProviderResult]] = {
    "claude": _run_claude,
    "gpt": _run_gpt,
}


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines:
        return stripped
    opener = lines[0].strip().casefold()
    if opener not in {"```", "```json"}:
        return stripped
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return "\n".join(lines[1:]).strip()


def _parse_json_object(text: str) -> dict[object, object]:
    candidate = _strip_code_fence(text)
    try:
        raw: object = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("model output did not contain a JSON object")
        try:
            raw = json.loads(candidate[start:end + 1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"model output was not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError("model output JSON root must be an object")
    return raw


def parse_classification(text: str, valid_codes: set[str]) -> Classification:
    raw = _parse_json_object(text)
    primary_domains = raw.get("primary_domains")
    rationale = raw.get("rationale")

    if not isinstance(primary_domains, list):
        raise RuntimeError("model output primary_domains must be a list")
    if len(primary_domains) > 3:
        raise RuntimeError("model output primary_domains must contain at most 3 entries")
    if not isinstance(rationale, str):
        raise RuntimeError("model output rationale must be a string")

    codes: list[str] = []
    seen: set[str] = set()
    invalid_values: list[str] = []
    for value in primary_domains:
        if not isinstance(value, str) or not value.strip():
            invalid_values.append(repr(value))
            continue
        code = value.strip().upper()
        if code not in seen:
            seen.add(code)
            codes.append(code)

    if invalid_values:
        raise RuntimeError(
            "model output primary_domains contains invalid entries: "
            + ", ".join(invalid_values)
        )

    unknown = [code for code in codes if code not in valid_codes]
    if unknown:
        raise RuntimeError("model output contains unknown SCF domains: " + ", ".join(unknown))

    return Classification(primary_domains=tuple(codes), rationale=rationale)


def classify_one(
    provider: str,
    document: DocumentRow,
    prompt: str,
    valid_codes: set[str],
) -> tuple[Classification, str]:
    prompts = [prompt, prompt + "\n\nReturn ONLY the JSON object, nothing else."]
    last_error = ""
    for attempt, attempt_prompt in enumerate(prompts, start=1):
        try:
            result = run_provider(provider, attempt_prompt)
            return parse_classification(result.text, valid_codes), result.model
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == len(prompts):
                break
    raise RuntimeError(f"{document.filename}: classification failed after retry: {last_error}")


def classify_documents(
    provider: str,
    entries: list[setup_fixture.ManifestEntry],
    domains: dict[str, tuple[str, str]],
    documents: dict[str, DocumentRow],
) -> tuple[list[ClassifiedDocument], str]:
    valid_codes = set(domains)
    classified: list[ClassifiedDocument] = []
    model_id = ""
    for entry in entries:
        document = documents[entry.filename]
        text = load_document_text(document)
        prompt, truncated = build_prompt(domains, text)
        classification, model = classify_one(provider, document, prompt, valid_codes)
        if not model_id:
            model_id = model
        if not classification.primary_domains:
            print(f"{document.filename}: empty primary_domains classification", file=sys.stderr)
        classified.append(
            ClassifiedDocument(
                filename=document.filename,
                classification=classification,
                truncated=truncated,
            )
        )
    if not model_id:
        raise RuntimeError(f"No model id recorded for provider {provider!r}")
    return classified, model_id


def cache_path_for(provider: str) -> Path:
    return BACKEND_ROOT / "fixtures-local" / f"intents_{provider}.json"


def write_cache(
    path: Path,
    provider: str,
    model: str,
    corpus_fingerprint: str,
    classified: list[ClassifiedDocument],
) -> None:
    if not path.parent.is_dir():
        raise RuntimeError(f"Cache directory does not exist: {path.parent}")

    payload = {
        "prompt_version": PROMPT_VERSION,
        "provider": provider,
        "model": model,
        "corpus_fingerprint": corpus_fingerprint,
        "classified_at": utc_now(),
        "documents": {
            item.filename: {
                "primary_domains": list(item.classification.primary_domains),
                "rationale": item.classification.rationale,
            }
            for item in classified
        },
    }

    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        if temp_name and Path(temp_name).exists():
            os.unlink(temp_name)
        raise


def print_summary(classified: list[ClassifiedDocument], cache_path: Path) -> None:
    empty_count = 0
    truncated_count = 0
    print("classification summary:")
    for item in classified:
        domains = ", ".join(item.classification.primary_domains) or "(empty)"
        print(f"{item.filename} -> {domains}")
        if not item.classification.primary_domains:
            empty_count += 1
        if item.truncated:
            truncated_count += 1
    print(f"documents classified: {len(classified)}")
    print(f"empty classifications: {empty_count}")
    print(f"truncated documents: {truncated_count}")
    print(f"cache path: {cache_path}")


def run(provider: str, force: bool) -> int:
    entries = setup_fixture._load_manifest(setup_fixture.MANIFEST_PATH)
    fingerprint = setup_fixture.corpus_fingerprint([entry.sha256 for entry in entries])
    manifest_filenames = {entry.filename for entry in entries}
    path = cache_path_for(provider)

    if not path.parent.is_dir():
        raise RuntimeError(f"Cache directory does not exist: {path.parent}")
    if path.exists() and not force:
        print(
            f"Intent cache already exists at {path}; pass --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    domains = load_domains()
    documents = load_document_rows(manifest_filenames)
    classified, model = classify_documents(provider, entries, domains, documents)
    write_cache(path, provider, model, fingerprint, classified)
    print_summary(classified, path)
    return 0


def main() -> int:
    args = parse_args()
    try:
        return run(args.provider, args.force)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
