#!/usr/bin/env python3
"""Report what the CDM retirement will drop — read-only, run BEFORE upgrading.

CDM retirement, phase 5 (#907, design doc ``docs/plans/cdm-retirement.md``
§6). Migration ``cdmdrop001`` drops the five ``cdm_*`` tables one-way; this
script tells you, per organisation, what is in them right now so the decision
to set ``SCF_CDM_DROP_ACK=1`` is an informed one and the purge report can be
checked afterwards. It issues SELECT statements only.

Per organisation it prints:

* ``settings->>'cdm_enabled'`` (the per-tenant override) and the effective
  flag as the retired module resolved it — tenant value if set, else the
  ``ENABLE_CDM`` environment variable (default false).
* Row counts for ``cdm_documents`` (by ``ingest_status``), ``cdm_mappings``
  (by ``status``), ``cdm_control_proposals`` (by ``status``),
  ``cdm_document_intents`` and ``cdm_document_chunks``.
* The object-storage key prefixes the purge will remove, derived from
  ``cdm_documents.organization_id`` + ``id`` (there is no ``storage_key``
  column): ``cdm/{organization_id}/{document_id}/``.
* The newest ``created_at`` in each table.

``--dump <file>`` additionally writes every row of the five tables as JSON,
for anyone who wants a copy outside the ``pg_dump`` (the chunk table's
``search_vector`` and ``body_norm`` are omitted: both derive from ``body``).

On a running rig the script is in neither the old image nor the old checkout
(``upgrade.sh`` checks the new tag out later). Take it from the release tag,
copy it into the container and run it from ``/tmp`` — never into the
bind-mounted tree::

    git fetch --tags
    git show tags/<target-tag>:backend/scripts/cdm_retirement_probe.py > /tmp/cdm_retirement_probe.py
    docker compose cp /tmp/cdm_retirement_probe.py backend:/tmp/
    docker compose exec backend python /tmp/cdm_retirement_probe.py
    docker compose exec backend python /tmp/cdm_retirement_probe.py --dump /tmp/cdm-rows.json
    docker compose cp backend:/tmp/cdm-rows.json ./cdm-rows.json

``DATABASE_URL`` is read from the environment, as the backend does. Paste the
output into the release PR for the tenant being upgraded.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

TABLES = (
    "cdm_documents",
    "cdm_document_chunks",
    "cdm_document_intents",
    "cdm_control_proposals",
    "cdm_mappings",
)
STATUS_COLUMN = {
    "cdm_documents": "ingest_status",
    "cdm_mappings": "status",
    "cdm_control_proposals": "status",
}
NEWEST_COLUMN = {"cdm_document_intents": "classified_at"}  # every other table: created_at


def _env_cdm_enabled() -> bool:
    # The retired services/cdm_tenancy.py: ENABLE_CDM defaulted to false and
    # only the literal "true" enabled it.
    return os.getenv("ENABLE_CDM", "false").lower() == "true"


def _effective(tenant_bool: Optional[bool]) -> bool:
    """The retired ``cdm_tenancy.get_tenant_cdm_enabled``: a JSON *boolean* in
    ``settings.cdm_enabled`` wins; anything else (absent, string, number)
    falls through to ``ENABLE_CDM``."""
    if isinstance(tenant_bool, bool):
        return tenant_bool
    return _env_cdm_enabled()


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def _table_exists(conn, table: str) -> bool:
    return bool((await conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table})).scalar())


async def _org_report(conn, org_id: Any, present: Dict[str, bool]) -> Dict[str, Any]:
    report: Dict[str, Any] = {"tables": {}, "key_prefixes": []}
    for table in TABLES:
        if not present[table]:
            report["tables"][table] = {"total": None, "note": "table absent"}
            continue
        newest_col = NEWEST_COLUMN.get(table, "created_at")
        # {table} and {newest_col} are identifiers taken from the module constants TABLES and
        # NEWEST_COLUMN; the organisation id stays a bind parameter (:o). Semgrep cannot see that,
        # hence the suppression (it only honours the comment on the line directly above the call).
        # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
        stmt = text(f"SELECT count(*), max({newest_col}) FROM {table} WHERE organization_id = :o")
        total, newest = (await conn.execute(stmt, {"o": org_id})).one()
        entry: Dict[str, Any] = {"total": int(total or 0), "newest": str(newest) if newest else None}
        status_col = STATUS_COLUMN.get(table)
        if status_col:
            # {status_col} comes from the STATUS_COLUMN constant; same shape as above.
            # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
            stmt = text(f"SELECT {status_col}, count(*) FROM {table} WHERE organization_id = :o "
                        f"GROUP BY {status_col} ORDER BY {status_col}")
            rows = (await conn.execute(stmt, {"o": org_id})).fetchall()
            entry["by_status"] = {str(s): int(n) for s, n in rows}
        report["tables"][table] = entry
    if present["cdm_documents"]:
        docs = (await conn.execute(
            text("SELECT id FROM cdm_documents WHERE organization_id = :o ORDER BY created_at"), {"o": org_id}
        )).fetchall()
        report["key_prefixes"] = [f"cdm/{org_id}/{doc_id}/" for (doc_id,) in docs]
    return report


#: Columns left out of ``--dump``: derived from ``body`` and rebuilt by the
#: database, and by far the largest text per chunk row.
DUMP_SKIP_COLUMNS = {"cdm_document_chunks": {"search_vector", "body_norm"}}


async def _dump_rows(conn, present: Dict[str, bool], dump: str) -> int:
    """Write every row of the five tables to ``dump`` as one JSON object
    ``{table: [row, ...]}``. Streams table by table into the open file so the
    peak in the backend container is one table's rows, not the whole corpus
    twice (rows + the serialised string)."""
    total = 0
    with open(dump, "w", encoding="utf-8") as fh:
        fh.write("{")
        for i, table in enumerate(TABLES):
            fh.write(("," if i else "") + json.dumps(table) + ": ")
            if not present[table]:
                fh.write("[]")
                continue
            # {table} iterates the TABLES constant, so the identifier is not user input.
            # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
            result = await conn.execute(text(f"SELECT * FROM {table}"))
            skip = DUMP_SKIP_COLUMNS.get(table, set())
            keys = [k for k in result.keys() if k not in skip]
            fh.write("[")
            first = True
            for row in result.mappings():
                fh.write("" if first else ",")
                json.dump({k: row[k] for k in keys}, fh, sort_keys=True, default=str)
                first = False
                total += 1
            fh.write("]")
        fh.write("}\n")
    return total


async def run(dump: Optional[str]) -> int:
    engine: AsyncEngine = create_async_engine(_database_url(), echo=False)
    try:
        async with engine.connect() as conn:
            present = {t: await _table_exists(conn, t) for t in TABLES}
            print(f"ENABLE_CDM (env) = {os.getenv('ENABLE_CDM', '<unset>')!r} -> {_env_cdm_enabled()}")
            missing = [t for t, ok in present.items() if not ok]
            if missing:
                print(f"tables absent (already dropped?): {', '.join(missing)}")
            # raw: the JSON value as text for display; tenant_bool: only a JSON boolean
            # counts as the override (the retired module ignored strings and numbers).
            orgs = (await conn.execute(
                text("SELECT id, name, settings::jsonb -> 'cdm_enabled' AS raw, "
                     "CASE WHEN jsonb_typeof(settings::jsonb -> 'cdm_enabled') = 'boolean' "
                     "THEN (settings::jsonb ->> 'cdm_enabled')::boolean END AS tenant_bool "
                     "FROM organizations ORDER BY name")
            )).fetchall()
            grand: Dict[str, int] = {t: 0 for t in TABLES}
            for org_id, name, raw, tenant_bool in orgs:
                report = await _org_report(conn, org_id, present)
                print(f"\n== {name} ({org_id})")
                shown = raw if raw is None else str(raw)
                print(f"   settings.cdm_enabled = {shown!r}; effective = {_effective(tenant_bool)}")
                for table, entry in report["tables"].items():
                    if entry.get("total") is None:
                        print(f"   {table}: {entry['note']}")
                        continue
                    grand[table] += entry["total"]
                    detail = f"; newest {entry['newest']}" if entry.get("newest") else ""
                    by_status = entry.get("by_status")
                    status_txt = f" {by_status}" if by_status else ""
                    print(f"   {table}: {entry['total']}{status_txt}{detail}")
                if report["key_prefixes"]:
                    print(f"   object-storage prefixes ({len(report['key_prefixes'])}):")
                    for prefix in report["key_prefixes"]:
                        print(f"     {prefix}")
            print("\n== totals: " + ", ".join(f"{t}={n}" for t, n in grand.items()))
            if sum(grand.values()):
                print("   cdmdrop001 will REFUSE without SCF_CDM_DROP_ACK=1 (rows exist).")
            else:
                print("   no CDM rows: cdmdrop001 runs without the ack.")
            if dump:
                n = await _dump_rows(conn, present, dump)
                print(f"\nwrote {n} row(s) to {dump} (chunk search_vector/body_norm omitted: derived from body)")
    finally:
        await engine.dispose()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only report of CDM data per organisation.")
    parser.add_argument("--dump", metavar="FILE", help="also write every cdm_* row as JSON to FILE")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.dump))


if __name__ == "__main__":
    sys.exit(main())
