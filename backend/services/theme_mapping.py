# SPDX-License-Identifier: AGPL-3.0-only
"""Which of a control's framework references feed the capability-theme mapping.

Capability themes are derived data: each control's NIST 800-53 references
reduce to control families, and ``capability_themes.json`` maps families to
themes. The references live in ``framework_mappings`` under a key that is a
slug of a workbook column header, so the key moves when the publisher renames
the column. SCF 2026.3 did exactly that (``nist_800_53_r5`` became
``nist_800_53_r5_2``). The derivation read the old spelling only, found
nothing on any current control, and every theme counted zero controls with no
error anywhere.

This module is the one place that names the key, so the seeder and the
catalogue upgrade cannot drift apart again.
"""
from __future__ import annotations

import re
from typing import Any, List, Mapping, Optional, Tuple

# ``nist_800_53_r5`` and its sub-releases (``nist_800_53_r5_2``), and a later
# revision when one ships. Deliberately NOT the baselines (``nist_800_53b_*``),
# the "no controls" variant (``nist_800_53_r5_noc``) or anything before
# revision 5, whose family set the theme rules were not written against.
_NIST_800_53_KEY = re.compile(r"^nist_800_53_r(\d+)(?:_(\d+))?$")
MIN_NIST_800_53_REVISION = 5


def nist_800_53_key_rank(key: str) -> Optional[Tuple[int, int]]:
    """(revision, sub-release) for a usable NIST 800-53 key, else None."""
    match = _NIST_800_53_KEY.match(key or "")
    if not match:
        return None
    revision = int(match.group(1))
    if revision < MIN_NIST_800_53_REVISION:
        return None
    return revision, int(match.group(2) or 0)


def nist_800_53_refs(framework_mappings: Optional[Mapping[str, Any]]) -> List[str]:
    """The control's NIST 800-53 references, from the newest edition it carries.

    A control normally carries one edition. When it carries two, the newest
    wins rather than the union: an older edition's references are the
    publisher's superseded view of the same control.
    """
    best: Optional[Tuple[Tuple[int, int], List[str]]] = None
    for key, refs in (framework_mappings or {}).items():
        rank = nist_800_53_key_rank(key)
        if rank is None or not refs:
            continue
        if best is None or rank > best[0]:
            best = (rank, list(refs))
    return best[1] if best else []


def nist_families(refs: List[str]) -> set:
    """Family codes of a list of references (``CM-08(01)`` -> ``CM``)."""
    return {ref.split("-")[0] if "-" in ref else ref for ref in refs}
