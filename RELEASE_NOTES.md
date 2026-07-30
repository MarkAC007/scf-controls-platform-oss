# v0.12.0

Adds the Document Map — a per-domain coverage view across all 33 SCF domains, separating what a person confirmed from what was merely suggested — on a rebuilt Control Documents mapper that runs entirely inside Postgres and quotes only verified character offsets. Migrations are additive (new cdm_document_intents table plus three columns on cdm_documents); nothing is enabled by default.

## What's new

- **Document Map** — a new view under *Knowledge Base* showing all 33 SCF
  domains as a coverage grid. Each domain reads as one of four states:
  *Confirmed* (a person accepted a mapping), *Suggested* (documents placed,
  nothing reviewed), *Gap* (controls scoped, no document), or *Not in scope*
  (a scoping decision, not an absence). Documents that reach no in-scope
  domain sit in an *Unmapped* rail rather than disappearing.
  - Confirmed and suggested are distinguished on four redundant channels —
    word, glyph, edge and strip — so the map stays readable in greyscale and
    colour is never the only signal.
  - The "Domains covered" headline counts **confirmed only**; suggestions
    never inflate it.
  - The view is strictly read-only. Every action routes to Control Documents.
- **Control Documents mapper rebuilt on Postgres** — two-tier full-text
  search with persisted score components, running entirely inside Postgres
  with no extra services. Every proposed mapping is anchored to verified
  character offsets that are re-read against the source text before display,
  so a quoted excerpt is always traceable to real words in the document.
  Mapping scores now break down into three stored components (text relevance,
  objective coverage, term overlap) and stay explainable after the fact.
- **Document placement (optional, off by default)** — classifies each
  uploaded document into the domains it appears to cover, so the map can
  place a document before any mapping is reviewed. It never creates, scores
  or cites a mapping; verified offsets remain the only mapping source.

## Upgrading

- Use `scripts/upgrade.sh v0.11.0` (read `UPGRADING.md` first). No breaking
  changes and no action required — every new capability is off by default and
  the stack behaves exactly as before until you enable it.
- To enable document placement, set `CDM_INTENT_PROVIDER` on **both** the
  backend and the Celery worker, then backfill existing documents:
  `docker compose exec backend python scripts/backfill_document_intents.py --dry-run`
  to preview, then `--apply`.
- Placement runs on a dedicated `cdm_intent` Celery queue. The bundled
  `docker-compose.yml` already lists it on the worker; if you run a
  **customised worker command**, add `cdm_intent` to its `-Q` list or
  classification tasks will queue unconsumed.
- Leaving placement disabled is fully supported, with one visible
  consequence: a document that yields no mappings sits in the Unmapped rail
  labelled "Awaiting classification", and the interface does not explain that
  the stage is switched off.

## Migrations

- `cdm2c709chunk` — adds `cdm_document_chunks` and persisted score components.
- `cdm3intent001` — adds the `cdm_document_intents` table and an index, plus
  three columns on `cdm_documents` (`intent_status`, defaulted to `pending`;
  `intent_error` and `intent_classified_at`, both nullable).
- Both are purely additive with working downgrades. No column is dropped, no
  type changed, and no existing row rewritten.
