"""
Input fingerprint computation for deterministic document regeneration.

Python port of ``scf-doc-gen`` ``src/meta/fingerprint.ts``.

The fingerprint is a composite SHA-256 of four independently-hashed components:

1. **controls_hash**  — Domain control data sorted by ``scf_id``, canonically serialised
2. **template_hash**  — System prompt / template body
3. **prompt_hash**    — Fully-constructed user prompt
4. **catalog_version** — Folded into the composite (platform addition, spec R6)

Same inputs -> same fingerprint -> skip regeneration.

**Why the catalog version is in the composite and not in ``controls_hash``:**
a catalog upgrade that rewords a control description already changes
``controls_hash``. But an upgrade that only *adds* controls outside the
document's scope would not, and the document would claim currency against a
catalog it was never generated from. Folding the version in makes staleness
honest at the cost of one advisory diff per upgrade — which is exactly what
spec R6 asks for ("never auto-regenerate").

Parity with the TypeScript original:
    With ``catalog_version=None`` this function is byte-for-byte identical to
    ``computeControlsFingerprint`` — same canonical JSON, same component hashes,
    same composite. Passing a catalog version appends it to the composite, which
    is the one deliberate divergence (spec R6) and is why the CLI's fingerprints
    remain comparable only for runs that predate catalog tracking.

Determinism guarantees:
    - Controls are sorted by ``scf_id`` before hashing (order-independent)
    - Assessment objectives within each control are sorted by ``ao_id``
    - Only generation-relevant fields are included in the hash
    - ``json.dumps(..., sort_keys=True, separators=(",", ":"))`` is canonical

Component isolation lets a caller attribute *why* a fingerprint changed:
new controls, an updated template, or a modified prompt.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


def sha256(value: str) -> str:
    """SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Canonical control serialisation
# ---------------------------------------------------------------------------

#: Fields extracted from a control for fingerprinting. Only data that would
#: affect generated output is included — a change to, say, ``created_at`` must
#: not invalidate a document.
_CONTROL_FIELDS = (
    "assessment_objectives",
    "control_description",
    "control_name",
    "implementation_notes",
    "implementation_status",
    "maturity_level",
    "owner",
    "scf_id",
)


def _canonical_control(control: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a control dict to its fingerprint-relevant fields.

    Assessment objectives are reduced to ``(ao_id, objective_text)`` pairs and
    sorted by ``ao_id`` so objective ordering from the database cannot perturb
    the hash.
    """
    objectives = control.get("assessment_objectives") or []
    reduced = sorted(
        (
            {"ao_id": ao.get("ao_id", ""), "objective_text": ao.get("objective_text", "")}
            for ao in objectives
        ),
        key=lambda ao: ao["ao_id"],
    )
    return {
        # camelCase deliberately: this dict is hashed, not read. The key name is
        # part of the JSON bytes, so it must match ``extractControlFingerprint``
        # in scf-doc-gen's ``src/meta/fingerprint.ts`` or a document carried over
        # from the CLI would look stale on its first platform run. Rename this and
        # every existing fingerprint silently invalidates.
        "assessmentObjectives": reduced,
        "control_description": control.get("control_description") or "",
        "control_name": control.get("control_name") or "",
        "implementation_notes": control.get("implementation_notes"),
        "implementation_status": control.get("implementation_status") or "",
        "maturity_level": control.get("maturity_level"),
        "owner": control.get("owner"),
        "scf_id": control.get("scf_id") or "",
    }


def _canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no incidental whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputComponents:
    """Breakdown of what data composed a fingerprint."""

    controls_hash: str
    template_hash: str
    prompt_hash: str
    control_count: int
    control_ids: List[str] = field(default_factory=list)
    catalog_version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "controls_hash": self.controls_hash,
            "template_hash": self.template_hash,
            "prompt_hash": self.prompt_hash,
            "control_count": self.control_count,
            "control_ids": list(self.control_ids),
            "catalog_version": self.catalog_version,
        }


@dataclass(frozen=True)
class FingerprintResult:
    """A composite fingerprint plus the components that produced it."""

    input_fingerprint: str
    input_components: InputComponents


def compute_fingerprint(
    controls: Sequence[Dict[str, Any]],
    system_prompt: str,
    user_prompt: str,
    catalog_version: Optional[str] = None,
) -> FingerprintResult:
    """Compute a deterministic fingerprint from generation inputs.

    Args:
        controls: Control dicts as built by :mod:`services.doc_gen.context`.
        system_prompt: The generator's system prompt or template body.
        user_prompt: The fully-constructed user prompt, control data interpolated.
        catalog_version: SCF catalog version the controls were read from.

    Returns:
        A :class:`FingerprintResult`. ``input_fingerprint`` is the value to
        compare against ``generated_documents.input_fingerprint`` to decide
        whether regeneration can be skipped.
    """
    ordered = sorted(controls, key=lambda c: (c.get("scf_id") or ""))
    canonical = [_canonical_control(c) for c in ordered]

    controls_hash = sha256(_canonical_json(canonical))
    template_hash = sha256(system_prompt or "")
    prompt_hash = sha256(user_prompt or "")

    composite = controls_hash + template_hash + prompt_hash
    if catalog_version:
        composite += catalog_version
    input_fingerprint = sha256(composite)

    return FingerprintResult(
        input_fingerprint=input_fingerprint,
        input_components=InputComponents(
            controls_hash=controls_hash,
            template_hash=template_hash,
            prompt_hash=prompt_hash,
            control_count=len(ordered),
            control_ids=[c.get("scf_id") or "" for c in ordered],
            catalog_version=catalog_version,
        ),
    )


def describe_change(previous: Dict[str, Any], current: InputComponents) -> List[str]:
    """Name which components changed between two fingerprints.

    Used by the UI's staleness column so it can say *what* moved rather than
    just "stale". ``previous`` is the stored ``input_components`` JSONB.
    """
    reasons: List[str] = []
    if not previous:
        return ["first generation"]

    if previous.get("controls_hash") != current.controls_hash:
        before = set(previous.get("control_ids") or [])
        after = set(current.control_ids)
        added, removed = after - before, before - after
        if added or removed:
            bits = []
            if added:
                bits.append(f"{len(added)} added")
            if removed:
                bits.append(f"{len(removed)} removed")
            reasons.append(f"controls {', '.join(bits)}")
        else:
            reasons.append(f"{current.control_count} controls changed")

    if previous.get("template_hash") != current.template_hash:
        reasons.append("template updated")
    if previous.get("prompt_hash") != current.prompt_hash:
        reasons.append("prompt updated")
    if previous.get("catalog_version") != current.catalog_version:
        reasons.append(
            f"catalog {previous.get('catalog_version')} → {current.catalog_version}"
        )

    return reasons
