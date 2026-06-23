# SCF Controls Platform

[![CI](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/ci.yml/badge.svg)](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/ci.yml)
[![CodeQL](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/codeql.yml/badge.svg)](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/codeql.yml)
[![Gitleaks](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/gitleaks.yml/badge.svg)](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/gitleaks.yml)
[![Semgrep](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/semgrep.yml/badge.svg)](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/semgrep.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/MarkAC007/scf-controls-platform-oss/badge)](https://scorecard.dev/viewer/?uri=github.com/MarkAC007/scf-controls-platform-oss)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)

A self-hosted **Governance, Risk & Compliance (GRC)** platform for managing cybersecurity
controls with the [Secure Controls Framework (SCF)](https://securecontrolsframework.com/).
Scope controls against 350+ frameworks (ISO 27001, SOC 2, NIST, PCI DSS, NIS2, and more),
track control maturity, run evidence-collection workflows, and assess inherent and residual
risk — all on your own infrastructure.

It ships as a **Docker Compose** stack with bundled PostgreSQL, Redis, and MinIO object
storage. No cloud account required.

**Learn more →** features, screenshots, and documentation at **[scfcontrolsplatform.com](https://scfcontrolsplatform.com/)**.

> **Bring your own SCF catalogue.** The SCF control content is licensed
> [CC BY-ND 4.0](https://creativecommons.org/licenses/by-nd/4.0/) and is **not** distributed
> with this project. You supply your own SCF Excel workbook (a free download from the SCF) and
> the included importer loads it. See [Load the SCF catalogue](#3-load-the-scf-catalogue).

---

## Contents

- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Loading the SCF catalogue](#3-load-the-scf-catalogue)
- [First-run setup](#5-create-your-organisation)
- [Accessing the platform](#accessing-the-platform)
- [Optional integrations](#optional-integrations)
- [How it works](#how-it-works)
- [Licensing](#licensing)

---

## Prerequisites

- **Docker** 24+ and the **Docker Compose** v2 plugin (`docker compose`, not `docker-compose`)
- ~4 GB free RAM and a few GB of disk for the database and evidence volumes
- An **SCF Excel workbook** (`.xlsx`) — download it free from
  [securecontrolsframework.com](https://securecontrolsframework.com/) (see the
  [SCF GitHub mirror](https://github.com/securecontrolsframework/securecontrolsframework) for
  background)

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/MarkAC007/scf-controls-platform-oss.git
cd scf-controls-platform-oss

# 2. Configure (see "Configuration" below — set DB_PASSWORD, API_KEY, OSS_SINGLE_TENANT=1)
cp .env.example .env
# edit .env

# 3. Load the SCF catalogue from your workbook (one-time)
mkdir -p catalog-source
cp /path/to/SCF-2025.4.xlsx ./catalog-source/scf.xlsx
docker compose --profile init run --rm catalog-importer

# 4. Start the stack
docker compose up -d

# 5. Create your organisation and grant yourself admin
docker compose exec backend python -m cli.admin setup
docker compose exec backend python -m cli.admin grant-admin --email you@example.com
```

Then open **http://localhost:5173**.

Each step is explained below.

---

## Configuration

Copy the example file and edit it. **Never commit your `.env`** — it holds secrets.

```bash
cp .env.example .env
```

### Required — change before you start

| Variable            | What to set                                                        |
| ------------------- | ------------------------------------------------------------------ |
| `DB_PASSWORD`       | A strong PostgreSQL password.                                      |
| `API_KEY`           | The master API key. Generate one: `openssl rand -hex 32`.          |
| `OSS_SINGLE_TENANT` | Set to `1` for a self-hosted single-tenant install (recommended). |

In single-tenant mode (`OSS_SINGLE_TENANT=1`) the master `API_KEY` acts as the admin for your
one organisation, and the browser-based catalogue upload is enabled. The backend refuses to
start in this mode if it detects more than one organisation or human member, so it stays safe.

### Sensible defaults — review, but fine to leave

| Variable                                       | Default                | Notes                                                   |
| ---------------------------------------------- | ---------------------- | ------------------------------------------------------- |
| `ENVIRONMENT`                                  | `production`           | `development` enables debug + reload.                   |
| `LOG_LEVEL`                                    | `info`                 | `debug` / `info` / `warning` / `error`.                 |
| `CATALOG_VERSION`                              | `2025.4`               | Label for your catalogue; match your workbook.          |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`      | `minioadmin`           | Bundled object-storage credentials — change for prod.   |
| `EVIDENCE_PUBLIC_ENDPOINT`                     | `http://localhost:9000`| URL the browser uses for evidence up/downloads.         |

See `.env.example` for the full, commented list.

---

## 3. Load the SCF catalogue

The platform needs the SCF control catalogue in its database. Because the SCF content is
licensed (CC BY-ND 4.0), you provide the workbook and the importer converts it — the project
ships the **importer code only**, never SCF data.

### Option A — CLI importer (works everywhere)

Place your workbook where the importer expects it (or set `SCF_XLSX` in `.env` to a custom
path), then run the one-shot importer container:

```bash
mkdir -p catalog-source
cp /path/to/SCF-2025.4.xlsx ./catalog-source/scf.xlsx
docker compose --profile init run --rm catalog-importer
```

The importer is version-agnostic: it auto-detects the SCF release from the workbook and writes
the generated JSON to `webclient/public/data/` (these files are git-ignored and never
committed). When you next start the backend, it seeds the catalogue into PostgreSQL
automatically.

### Option B — upload in the browser

In single-tenant mode (`OSS_SINGLE_TENANT=1`), if the catalogue is empty the app shows a
first-run onboarding screen at http://localhost:5173. Drop your SCF `.xlsx` there and it
imports and seeds in the background — no CLI needed.

---

## 4. Start the stack

```bash
docker compose up -d
```

On first boot the backend **runs database migrations automatically** (Alembic) and
**seeds the catalogue** if it is empty — there is no manual migration step. Watch progress with:

```bash
docker compose logs -f backend
```

The API is healthy when `GET http://localhost:8000/health` returns `{"status": "healthy"}`.

---

## 5. Create your organisation

Create the default organisation and make yourself an administrator:

```bash
# Create the organisation (optionally: --name "Your Company")
docker compose exec backend python -m cli.admin setup

# Grant platform admin to your account
docker compose exec backend python -m cli.admin grant-admin --email you@example.com

# Or add a member directly (no OAuth required)
docker compose exec backend python -m cli.admin add-member \
  --email you@example.com --org-slug default --role admin
```

Run `docker compose exec backend python -m cli.admin --help` for the full command list
(also documented in `backend/cli/README.md`).

---

## Accessing the platform

| Service              | URL                              | Notes                                   |
| -------------------- | -------------------------------- | --------------------------------------- |
| **Web app**          | http://localhost:5173            | The main interface.                     |
| **API**              | http://localhost:8000            | REST API.                               |
| **API docs**         | http://localhost:8000/docs       | Interactive Swagger UI.                 |
| **MinIO console**    | http://localhost:9001            | Object-storage admin (evidence files).  |

By default the platform authenticates with the `API_KEY` you set — no external identity
provider is required.

---

## Optional integrations

All of these are off by default; enable only what you need in `.env`.

- **Google OAuth** — set `GOOGLE_AUTH_ENABLED=true` (and the matching `VITE_GOOGLE_AUTH_ENABLED=true`)
  plus `GOOGLE_CLIENT_ID` / `VITE_GOOGLE_CLIENT_ID` to let users sign in with Google.
- **Object storage** — the bundled MinIO works out of the box. To use **AWS S3** or **Azure Blob**
  instead, set the corresponding `AWS_*` or `AZURE_STORAGE_*` variables.
- **AI assessment** — set `ANTHROPIC_API_KEY` to enable Claude-powered evidence and control
  assessments.
- **Email notifications** — set `RESEND_API_KEY` and `RESEND_FROM_EMAIL` to send outbound email.
- **Vendor research** — set `HIBP_API_KEY` / `NVD_API_KEY` for breach and CVE lookups.

---

## How it works

| Component        | Role                                                                    |
| ---------------- | ----------------------------------------------------------------------- |
| **backend**      | FastAPI REST API (port 8000), runs migrations and seeds on startup.     |
| **frontend**     | React + Vite web app (port 5173).                                       |
| **postgres**     | PostgreSQL 15 — the system of record.                                   |
| **redis**        | Cache and Celery broker (internal network only).                        |
| **celery-worker / celery-beat** | Async tasks: catalogue import, evidence assessment, scheduling. |
| **minio**        | S3-compatible storage for evidence files (ports 9000 / 9001).           |
| **catalog-importer** | One-shot container (profile `init`) that converts your SCF `.xlsx`. |

The SCF workbook is converted to JSON once, seeded into PostgreSQL, and the database is the
runtime source of truth thereafter. To load a newer SCF release, re-run the importer and
re-seed (`docker compose exec backend python -m cli.admin seed-catalog --force --confirm`).

---

## Licensing

- **Software (this repository):** [GNU Affero General Public License v3.0](https://www.gnu.org/licenses/agpl-3.0.html)
  — see [`LICENSE`](LICENSE).
- **SCF catalogue content:** [Creative Commons Attribution-NoDerivatives 4.0 (CC BY-ND 4.0)](https://creativecommons.org/licenses/by-nd/4.0/),
  © the Secure Controls Framework. **Not** included here — download your own workbook from
  [securecontrolsframework.com](https://securecontrolsframework.com/).

This project is not affiliated with or endorsed by the Secure Controls Framework.
