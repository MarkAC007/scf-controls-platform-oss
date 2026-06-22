# Web Client Data Directory

## Overview

This directory contains **two types of data** with different purposes:

## 1. SCF Library Files (Generated — NOT committed)

These JSON files hold SCF catalog data and are the runtime seed source. **They are NOT
distributed in this repository.** SCF control content is licensed (CC BY-ND 4.0 + a separate
commercial licence) and cannot be redistributed here. Instead they are **generated at install
time** from your own SCF Excel workbook by the catalogue importer
(`scripts/extract_scf_data.py`, run via `docker compose --profile init run --rm catalog-importer`).
They are git-ignored. See the root `README.md` → "Bring your own SCF Excel catalogue".

- `control_guidance.json` - SCF control definitions with full metadata
- `domains.json` - SCF domain definitions
- `erl.json` - Evidence Request List entries
- `assessment_objectives.json` - Assessment objectives for controls
- `frameworks.json` / `controls_mapping.json` - Framework mapping definitions
- `catalog_meta.json` - Importer-written metadata (detected SCF version + extracted counts)

> The curated, **non-SCF** files in this directory (`capability_themes.json`,
> `collection_interfaces.json`, `evidence_templates.json`, `system_collection_recipes.json`)
> are ComplianceGenie's own content and **are** committed.

### How Data Loading Works

On application startup:
1. Backend reads these JSON files
2. Data is seeded into PostgreSQL catalog tables (if empty)
3. API serves data from the database

The database seeder (`backend/catalog_seeder.py`) handles this automatically.

### ⚠️ Important - Editing These Files

Changes to these files will only take effect after:
1. Clearing the database (so tables are empty)
2. Restarting the application (triggers re-seeding)

In normal operation, the database is the runtime source of truth.

## 2. Legacy Application State File

- `scoped_controls.json` - **Legacy file, now stored in database**

Note: Control scoping is now managed through the database and API.
This file may exist for historical reference but is not used at runtime.

## Data Architecture

```
SCF Library (JSON → Database)
  Source: /webclient/public/data/*.json
         ↓ (seeded at startup)
  Database: scf_catalog_* tables
         ↓ (served via API)
  Web UI displays controls

User Data (Database)
  Database: scoped_controls, evidence_tracking, etc.
         ↓ (API endpoints)
  Web UI manages control scoping
```

## Why This Architecture?

1. **Single Source of Truth**
   - JSON files define the SCF catalog
   - Database provides efficient runtime queries
   - API ensures consistent access patterns

2. **Web Browser Requirements**
   - Browsers can fetch these files directly for initial load
   - Database queries handle complex filtering/pagination

3. **Separation of Concerns**
   - Catalog data = reference data from SCF (read-only)
   - User data = organisation-specific decisions (read-write via API)

## Related Documentation

- See `/README.md` for complete architecture
- See `/webclient/README.md` for web client documentation
- See `/backend/catalog_seeder.py` for seeding logic
