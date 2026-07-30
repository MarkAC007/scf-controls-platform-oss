"""Single definition of the CDM document-intent classification prompt.

The prompt lived in ``scripts/cdm_eval/classify_intents.py`` while intent
classification was a host-side measurement experiment. The runtime classifier
must send the identical prompt, so the text moved here and the eval script now
imports it. Two copies would let the measured accuracy and the shipped
behaviour drift apart silently.

``PROMPT_VERSION`` is part of the measurement contract. Bump it whenever the
prompt text, SCF domain rendering, document truncation limit, or required
output shape changes. Rows and caches carrying different prompt versions are
not comparable; treating them as comparable would be a measurement error rather
than an experimental result.

The prompt uses document text only. The original filename is excluded because
the eval's ground-truth judge keys off filename substrings; showing filenames
to the classifier would recreate a circular answer key.
"""
from __future__ import annotations

PROMPT_VERSION = "1"
MAX_DOCUMENT_CHARS = 30_000


def build_prompt(domains: dict[str, tuple[str, str]], document_text: str) -> tuple[str, bool]:
    truncated = len(document_text) > MAX_DOCUMENT_CHARS
    prompt_text = document_text[:MAX_DOCUMENT_CHARS] if truncated else document_text
    truncation_note = (
        "\nThe document text below was truncated at "
        f"{MAX_DOCUMENT_CHARS} characters and is therefore partial.\n"
        if truncated
        else "\n"
    )
    domain_lines = "\n".join(
        f"{identifier} — {name}: {principle}"
        for identifier, (name, principle) in domains.items()
    )

    # The filename is deliberately absent: ground truth is filename-based, so
    # including it would make the classifier a circular measurement shortcut.
    prompt = (
        "You are classifying an organisational policy document against the SCF domain catalogue.\n\n"
        "SCF domain catalogue:\n"
        f"{domain_lines}\n\n"
        "Return a STRICT JSON object and nothing else, exactly shaped as:\n"
        '{"primary_domains": ["..."], "rationale": "<=40 words"}\n\n'
        "primary_domains is the 1 to 3 SCF domain codes for which this document is the "
        "AUTHORITATIVE organisational policy - its primary subject matter. A domain merely "
        "mentioned, referenced, cross-referenced, or incidentally supported is NOT primary. "
        "If the document is not the organisation's policy for a domain, it must not be listed. "
        "An empty list is acceptable when the document is authoritative for nothing in the catalogue."
        f"{truncation_note}"
        "Document text:\n"
        f"{prompt_text}"
    )
    return prompt, truncated
