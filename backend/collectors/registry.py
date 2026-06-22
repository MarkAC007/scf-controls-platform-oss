"""Collector registry: declarative (collector_id, source_label) -> artifact_types mapping.

See epic #569 M2 design spec on issue #572 (2026-04-18). This module exists to
replace the heuristic `_guess_artifact_type_for_source` over time — in PR 1
behaviour is controlled by ENABLE_COLLECTOR_REGISTRY and defaults to off, so
the heuristic remains authoritative until collectors opt in.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parent / "registry.yml"
_SUPPORTED_REGISTRY_VERSION = 1
_cache: Optional[dict] = None


def _load_registry() -> dict:
    """Load and index registry.yml once per process."""
    global _cache
    if _cache is not None:
        return _cache

    try:
        with _REGISTRY_PATH.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("collector registry not found at %s", _REGISTRY_PATH)
        _cache = {"by_source": {}, "by_collector_source": {}}
        return _cache

    version = raw.get("registry_version")
    if version != _SUPPORTED_REGISTRY_VERSION:
        logger.warning(
            "collector registry version %r not supported (expected %r) — falling through to heuristic",
            version, _SUPPORTED_REGISTRY_VERSION,
        )
        _cache = {"by_source": {}, "by_collector_source": {}}
        return _cache

    by_source: dict[str, list[str]] = {}
    by_collector_source: dict[tuple[str, str], list[str]] = {}
    for row in raw.get("sources") or []:
        source_label = row.get("source_label")
        if not source_label:
            continue
        atypes = [a.get("type") for a in (row.get("artifact_types") or []) if a.get("type")]
        if not atypes:
            continue
        by_source[source_label] = atypes
        collector_id = row.get("collector_id")
        if collector_id:
            by_collector_source[(collector_id, source_label)] = atypes

    _cache = {"by_source": by_source, "by_collector_source": by_collector_source}
    return _cache


def reset_cache() -> None:
    """Testing hook: drop the cached registry so the next load re-reads disk."""
    global _cache
    _cache = None


def _flag_enabled() -> bool:
    return os.getenv("ENABLE_COLLECTOR_REGISTRY", "false").lower() == "true"


def resolve_artifact_types(
    collector_id: Optional[str],
    source_label: Optional[str],
    *,
    declared: Optional[list[str]] = None,
) -> tuple[list[str], str]:
    """Resolve artifact types for an evidence file.

    Returns (types, resolved_via) where resolved_via ∈
      {"payload", "registry", "empty"}.

    The call site is responsible for handling the heuristic fallback when this
    returns ("[]", "empty"). Keeping the heuristic outside of this module
    preserves M1a behaviour when ENABLE_COLLECTOR_REGISTRY=false (in which
    case we short-circuit to "empty").
    """
    if declared:
        normalised = [t for t in declared if t]
        if normalised:
            _log_resolution(collector_id, source_label, "payload", normalised)
            return normalised, "payload"

    if not _flag_enabled():
        return [], "empty"

    reg = _load_registry()
    if collector_id and source_label:
        hit = reg["by_collector_source"].get((collector_id, source_label))
        if hit:
            _log_resolution(collector_id, source_label, "registry", hit)
            return list(hit), "registry"

    if source_label:
        hit = reg["by_source"].get(source_label)
        if hit:
            _log_resolution(collector_id, source_label, "registry", hit)
            return list(hit), "registry"

    _log_resolution(collector_id, source_label, "empty", [])
    return [], "empty"


def _log_resolution(
    collector_id: Optional[str],
    source_label: Optional[str],
    resolved_via: str,
    types: list[str],
) -> None:
    logger.info(
        "collector.resolve collector_id=%r source_label=%r resolved_via=%s types=%s",
        collector_id, source_label, resolved_via, types,
    )
