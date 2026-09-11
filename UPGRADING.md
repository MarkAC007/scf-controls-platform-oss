# Upgrading the SCF Controls Platform

This guide covers upgrading a self-hosted (docker-compose) deployment in place,
safely, without losing data.

> ## ⚠ Never run `docker compose down -v`
>
> The `-v` flag **deletes the named volumes** — your entire Postgres database
> **and** every MinIO evidence blob — with no undo. Stopping the stack is fine
> (`docker compose stop` / `docker compose down`), but **never** add `-v`. The
> upgrade tool only ever uses `docker compose up -d --build`, which preserves
> your volumes.

---

## 1. Before you upgrade

1. **Check the in-app badge.** When a newer release exists, the footer shows an
   "Update available → vX.Y.Z" badge, and the Database Stats → Version
   Information panel shows what you're on, what's latest, whether it's breaking,
   and a link to the release notes.
2. **Read the release notes.** Especially if the release is flagged **breaking**.
   The notes and the upgrade manifest tell you about new required settings and
   any migration of note.
3. **Pick a maintenance window.** The upgrade briefly stops the application
   (backend + workers) while it backs up and migrates. Postgres and MinIO stay
   up for the backup.

### Credentials during an upgrade

Nothing about your credentials is forced to change. The platform resolves every
credential in the order **database, then `NAME_FILE`, then the plain environment
variable**, and the environment tier is unchanged — so an install whose values
are still in `.env` keeps working exactly as it did, with no migration.

Three things `scripts/upgrade.sh` now does for you:

- **`COMPOSE_FILE` is honoured** from the environment, then from a
  `COMPOSE_FILE=` line in `.env`, then falling back to `docker-compose.yml`. A
  colon-separated list becomes multiple `-f` flags, so an install using the
  file-backed credential overlay
  (`docker-compose.yml:docker-compose.secrets.yml`) is validated, scanned and
  rebuilt as that overlay rather than as the base file alone. When nothing is
  configured, no `-f` is passed at all and compose keeps its own discovery, so
  `docker-compose.override.yml` still loads automatically. Note that setting
  `COMPOSE_FILE` yourself **disables** that auto-discovery — add the override
  file to the list if you use one.
- **`SCF_SECRET_KEY` is generated if absent, before migrations run.** It
  encrypts tier-3 integration credentials. An install with a secrets directory
  gets a `0600` file at `$SCF_SECRETS_DIR/SCF_SECRET_KEY`; a legacy install gets
  an `SCF_SECRET_KEY=` line appended to `.env`. An existing key is **never**
  overwritten — doing so would make every already-encrypted value permanently
  unreadable. The ordering matters: no migration is allowed to require a secret
  that did not exist before the upgrade, so the key is minted before the
  migration one-shot rather than during it.
  **Back the key up.** Encrypted values are unrecoverable without it.
- **A missing key is a hard stop only when it matters.** If `SCF_SECRET_KEY` is
  absent while the `integration_secrets` table already holds rows, the upgrade
  refuses to start and tells you to restore the key first. If the table is empty
  or does not exist yet, it warns and continues.

Backups now include the credential directory. `scripts/backup.sh` adds
`backups/secrets-<TS>.tar.gz` (mode `0600`, excluding the provisioning token) to
each set and covers it in the checksum file, and `--rollback` restores it into
`SCF_SECRETS_DIR` — setting the current credentials aside first — so a rolled-back
database and the key that encrypted it move together. On a legacy install with
no `SCF_SECRETS_DIR`, backup.sh says so plainly: your credentials are in `.env`
and you must back that file up separately.

After upgrading, convert any legacy plaintext webhook secrets and invite tokens
at your convenience:

```bash
docker compose exec backend python -m cli.admin backfill-encrypt
docker compose exec backend python -m cli.admin secrets-status
```

Both are idempotent, and `secrets-status` never prints a value. To rotate the
key itself, see the rotation procedure in the
[Credentials and secrets](https://markac007.github.io/scf-controls-platform/admin-guide/secrets/)
guide — in short, prepend the new key to the comma-separated list, restart, run
`rotate-secret-key`, and remove the old key only once it reports zero rows
remaining.

### Upgrade path from a `.env` install

**This release is not a breaking change.** `scripts/upgrade.sh` on an existing
install works with the credentials left in `.env`. One additive migration runs
(`intsec947a1`: a new `integration_secrets` table, a new `platform_audit_log`
table, two widened columns and a lookup hash on the invite tables — see
[docs/MIGRATIONS.md](docs/MIGRATIONS.md)). It needs no `SCF_SECRET_KEY` and
encrypts nothing; legacy plaintext values stay readable afterwards. Nothing is
forced: no credential moves, no format changes, no re-entry. You then have
three routes.

- **Stay on `.env`.** Nothing to do. The upgrade generates an `SCF_SECRET_KEY`
  and appends it to `.env` (or fills in an empty `SCF_SECRET_KEY=` line copied
  from an old `.env.example`). Every integration credential you store from then
  on is encrypted with it, so **back `.env` up** — `scripts/backup.sh` writes no
  credential tarball on a legacy install and warns you to copy the file
  yourself. Placeholder credentials are no longer accepted outside
  `ENVIRONMENT=development` or `test`: the backend refuses to boot on a
  shipped-placeholder `API_KEY`, `DB_PASSWORD` or, with `AWS_ENDPOINT_URL`
  set, AWS key pair, and says which one.
- **Move to file-backed credentials.** Run `scripts/install.sh --import-env`
  (add `--secrets-dir /abs/path` to choose the directory; the default is
  `$HOME/.scf/secrets`). It reads `.env`, writes one `0600` file per
  credential (`DB_PASSWORD`, `SCF_SECRET_KEY`, `API_KEY`,
  `DOWNLOAD_TOKEN_SECRET`, the MinIO root pair, the AWS pair,
  `KC_ADMIN_PASSWORD`, `OIDC_CLIENT_SECRET`) into a `0700` directory, never
  overwriting a file that already exists, leaving a placeholder as an empty
  file and minting `SCF_SECRET_KEY` only if it is absent. It copies the old
  file aside as `.env.bak.<timestamp>` (mode `0600`), then rewrites `.env`
  without the credential lines and with `SCF_SECRETS_DIR=` and
  `COMPOSE_FILE=docker-compose.yml:docker-compose.secrets.yml` appended. On
  Linux it grants group `1001` read access so the service containers can read
  the files. Then `docker compose up -d`; compose reads `COMPOSE_FILE` from
  `.env` and recreates the services whose configuration changed. **Why
  `--import-env` and not a fresh run:** `POSTGRES_PASSWORD_FILE` is read by
  `initdb` only, so on an existing `postgres_data` volume the database keeps
  its old password no matter what the file says. `--import-env` copies your
  *current* `DB_PASSWORD` into the file verbatim, so the role and the file
  agree. A bare `scripts/install.sh` on a checkout that has a `.env` refuses to
  run for exactly this reason, and a second `--import-env` refuses once the
  directory is marked `.provisioned`. Delete `.env.bak.<timestamp>` once the
  stack is confirmed up; it still holds every credential in clear.
- **Clean re-install.** Fresh checkout, then `scripts/install.sh --up` (no
  `.env` present, so the wizard runs and generates everything). Bring the data
  across from a `scripts/backup.sh` set taken on the old install, or from the
  in-app backup (`GET /api/database/backup`, restored with
  `POST /api/database/restore`). Two things do not carry over on their own:
  the new install has a **new `SCF_SECRET_KEY`**, so integration credentials
  encrypted under the old key must be re-entered unless you copy the old key
  across first (the old `SCF_SECRET_KEY` file, or the `SCF_SECRET_KEY=` line
  from the old `.env`, into `$SCF_SECRETS_DIR/SCF_SECRET_KEY`); and the new
  `DB_PASSWORD` applies only to a **fresh Postgres volume** — if you reuse the
  old `postgres_data` volume, the role still has the old password and the
  stack will not authenticate until you `ALTER ROLE` it or put the old value
  in the `DB_PASSWORD` file. Note that `scripts/upgrade.sh --rollback <TS>`
  restores the *whole* set — including the credential tarball and the git ref
  recorded at backup time — so on a fresh checkout prefer restoring the dump
  into the new, empty database by hand (the fresh-database shape in
  [docs/runbooks/backup-restore.md](docs/runbooks/backup-restore.md)) plus the
  MinIO tar, and copy only the key.

**What still needs a hand** after any of the three:

- **Rotating the Postgres role password** is a manual, three-step procedure
  (`ALTER ROLE` first, then the file, then recreate the services). Nothing
  automates it, because `POSTGRES_PASSWORD_FILE` has no effect on an existing
  database. See the
  [Credentials and secrets](https://markac007.github.io/scf-controls-platform/admin-guide/secrets/)
  guide.
- **`VITE_API_KEY` moves with `--import-env` in effect, but not by copying.**
  The frontend bundle still carries the API key at image build time. On an
  install that uses the secrets overlay the build reads it from
  `$SCF_SECRETS_DIR/API_KEY`, but only on the API-key sign-in path: when
  `VITE_OIDC_ENABLED` or `VITE_GOOGLE_AUTH_ENABLED` is `true` the file is
  ignored and the bundle carries whatever `VITE_API_KEY` says (blank, if you
  built it blank). So `.env` no longer needs a `VITE_API_KEY` line on the
  API-key path; a stale one left behind is harmless because the file takes
  precedence. Rotating the key still needs `docker compose up -d --build
  frontend`, which picks up the new value. The trust-boundary consequence in
  `SECURITY.md` (anyone who can load the UI holds the master key in
  single-tenant mode without an identity provider) is not changed by this
  release for any sign-in mode.
- **MinIO console and S3 ports are unchanged.** The secrets overlay adds no
  port mappings; `MINIO_PORT` and `MINIO_CONSOLE_PORT` behave as before.
- **`COMPOSE_PROFILES` is not written by `--import-env`.** An existing
  `COMPOSE_PROFILES=idp` line in `.env` is kept, because only credential lines
  are stripped, but no line is added. If you enable the bundled identity
  provider by passing `--profile idp` on the command line, keep passing it
  (`scripts/upgrade.sh` inherits it from the environment or `.env` the same
  way it does today).

---

## 2. Run the upgrade

From the **Docker host**, in the **repository root**:

```bash
scripts/upgrade.sh v0.9.0
```

The tool walks through six phases and refuses (changing nothing) if any
precondition fails:

1. **Load the manifest** for the target tag from the GitHub Release (or supply a
   local one with `--manifest FILE` for air-gapped installs). A release with no
   manifest is refused — there is nothing safe to reason about.
2. **Preflight** — clean working tree, version floor (`min_upgradable_version`),
   no downgrade, `.env` drift warnings, disk-space, floating-image warnings,
   compose validity. Nothing is changed here.
3. **Quiesce + backup** — stops the app, then takes a **mandatory, validated**
   backup of **both** stores: a `pg_dump` (custom format) of Postgres and a tar
   of the MinIO evidence volume. Both are checksummed and made read-only. If
   either fails, the app is restarted and the upgrade aborts — nothing changed.
4. **Checkout + migrate** — checks out the target tag, rebuilds the backend
   image, and runs `alembic upgrade head` as a one-shot (workers stay stopped so
   nothing races the schema change), then starts the full stack.
5. **Verify the running code** — waits for `/health`, checks the database is at
   the code's Alembic head, and checks the rebuilt image's baked build stamp.
   **If any check fails, it rolls back automatically** (see §4).
6. **Done** — prints the new version and where your backups live.

Add `--yes` to skip the confirmation prompt (for unattended runs).

---

## 3. After upgrading

- **Refresh your browser** to load the new UI. The footer badge clears once the
  installed version matches the latest release.
- Your backups remain under `./backups/` (write-protected). Keep them until you
  are confident in the new version. The credential tarball in each set,
  `secrets-<TS>.tar.gz`, is left at mode `0600` rather than write-protected.
- **If the upgrade generated an `SCF_SECRET_KEY`, back it up now.** It is at
  `$SCF_SECRETS_DIR/SCF_SECRET_KEY`, or on the last line of `.env` on a legacy
  install. Every integration credential you store from here on is encrypted with
  it and unrecoverable without it.

### Version-specific notes

- **The Control Documents Mapper (CDM) is retired; migration `cdmdrop001`
  drops its five tables (`cdm_documents`, `cdm_document_chunks`,
  `cdm_document_intents`, `cdm_control_proposals`, `cdm_mappings`) and removes
  the per-tenant `cdm_enabled` setting.** The routes and UI left in the same
  release window, so nothing reads these tables any more. The drop is one-way:
  the next release cannot restore the rows, only the pre-upgrade backup can.
  Uploaded CDM files are not touched by the migration — a script removes them
  afterwards.

  1. **Before upgrading**, see what will go. The probe is read-only. It is in
     neither the old image nor your current checkout (`upgrade.sh` checks the
     new tag out later), so take it from the release tag, copy it into the
     running backend and paste the output into your change record:

     ```bash
     git fetch --tags
     git show tags/vX.Y.Z:backend/scripts/cdm_retirement_probe.py > /tmp/cdm_retirement_probe.py
     docker compose cp /tmp/cdm_retirement_probe.py backend:/tmp/
     docker compose exec backend python /tmp/cdm_retirement_probe.py
     ```

     If every count is 0, the migration runs without step 2.
  2. **If any `cdm_*` table holds rows, add `SCF_CDM_DROP_ACK=1` to `.env`
     before you run `upgrade.sh`.** Without it the migration refuses and lists
     the counts — and inside `upgrade.sh` a refused migration is a failed
     upgrade, so the script **rolls the whole upgrade back automatically**
     (database swap + object-store restore + rebuild of the old version). Safe,
     but slow, and you end up where you started. With the ack set, the upgrade
     runs through; `upgrade.sh` has taken its backup (`./backups/<ts>_*`) before
     the migration. **Copy that backup set somewhere `backup.sh` will not prune
     it** — it is the only copy of the rows — then remove the variable. It is
     honoured by this one migration only.
  3. **After the upgrade**, remove the uploaded files. Dry run first, then
     apply, then confirm nothing is left (the script is idempotent):

     ```bash
     docker compose exec backend python scripts/cdm_retirement_purge.py            # report only
     docker compose exec backend python scripts/cdm_retirement_purge.py --apply    # delete
     docker compose exec backend python scripts/cdm_retirement_purge.py            # expect 0 objects
     ```

     The purge deletes every object under the `cdm/` prefix of the evidence
     store and nothing else (evidence lives under `evidence/`). On a versioned
     S3 bucket the deleted keys survive as non-current versions; the script
     reports the versioning state and leaves that decision to you. The Azure
     Blob path is untested against a live account — run the dry run first.
  4. **Rollback** is `scripts/upgrade.sh --rollback <ts>` with the backup from
     step 2, which restores both the rows and the files. An Alembic downgrade
     of `cdmdrop001` recreates the five tables empty and is for development
     databases only. If you only want to *read* the old rows again, restore the
     five tables from the copied `pg_dump -Fc` file into a scratch database
     instead of rolling back (table definitions and rows only, no constraints):

     ```bash
     docker compose exec -T postgres createdb -U "$DB_USER" cdm_scratch
     docker compose exec -T postgres pg_restore -U "$DB_USER" -d cdm_scratch \
       -t cdm_documents -t cdm_document_chunks -t cdm_document_intents \
       -t cdm_control_proposals -t cdm_mappings < ./backups/<ts>_v<version>.dump
     ```
  5. Remove the dead configuration. In `.env`: `ENABLE_CDM`, every `CDM_*`
     variable, `ENABLE_CDM_LIGHTRAG` and `LIGHTRAG_BASE_URL` — nothing reads
     them any more. If you run the Celery worker with a custom `-Q` list, drop
     `cdm` and `cdm_intent` from it; those queues no longer exist. The
     per-tenant `settings.cdm_enabled` is removed by the migration itself.

- **Evidence collection tasks gain a tenant and an optional owning team
  (migration `evtaskteam1`).** This release lets a single evidence item's tasks
  be owned by different teams — engineering wires up the log export, the
  platform team collects it, GRC signs it off — and closes a tenancy gap in the
  same table. The migration is applied by phase 4 of the upgrade like any other;
  there is nothing extra to run.

  > **⚠ This migration requires PostgreSQL 15 or newer.**
  >
  > It is the first place in the platform to use `ON DELETE SET NULL (column)`,
  > the column-list form of the referential action that **PostgreSQL 15
  > introduced**. On PostgreSQL 14 or earlier the `ALTER TABLE` is a syntax
  > error and the upgrade will fail at phase 4 — the transaction rolls back, so
  > nothing is half-applied, but the release will not install.
  >
  > No supported deployment is affected: PostgreSQL 15 is what this platform
  > already pins and documents everywhere — `postgres:15-alpine` in
  > `docker-compose.yml`, `docker-compose.dev-demo.yml` and
  > `docker-compose.prod-test.yml`, and PostgreSQL 15 in both
  > READMEs. What changes is that the floor is now **load-bearing rather than
  > conventional**. If you self-host against your own PostgreSQL instance rather
  > than the bundled container, check its version with `SELECT version();`
  > before upgrading.

  It is **schema-only and additive**:

  - `evidence_collection_tasks` gains `organization_id` (`NOT NULL`) and
    `owning_team_id` (nullable), plus two composite foreign keys that force the
    parent evidence item and the owning team to belong to the same organisation
    as the task. Until now this table had **no organisation column at all** —
    its tenancy was only transitive, through `evidence_tracking` — so nothing at
    the database level stopped a task pointing at another tenant's team. That is
    what the new columns close.
  - **The backfill is derived, not guessed.** `organization_id` is populated by
    a mechanical join along `evidence_tracking_id`, which is already `NOT NULL`
    with a real foreign key behind it: every task has exactly one parent and
    every parent has exactly one organisation. The column is added nullable,
    backfilled, and only then constrained — so if any row failed to resolve, the
    `SET NOT NULL` aborts and the whole migration rolls back rather than
    half-applying. Nothing is inferred from the free-text `evidence_tracking.owner`
    column; that reconciliation is a separate, operator-run, dry-run-first
    exercise in a later release.
  - **`owning_team_id` is NULL for every existing row, and NULL means inherit.**
    A task with no owning team follows its evidence item's accountable team, so
    no task changes hands on upgrade and no organisation has anything to set.
    Setting it overrides the parent for that one task.
  - **Deleting a team never deletes tasks.** The team-side foreign key is
    `ON DELETE SET NULL` on `owning_team_id` alone, so removing a team returns
    its tasks to inheriting from their evidence item. The task, its history and
    its assignee are untouched. (The single-column form of `SET NULL` would try
    to null `organization_id` too and fail against its `NOT NULL`, which is what
    makes the PostgreSQL 15 syntax above necessary rather than stylistic.)
  - **Two new indexes.** One on `evidence_collection_tasks(owning_team_id)`, so
    "which tasks does this team own" and the team-delete referential action do
    not sequentially scan every task in the deployment. One on
    `notifications(type, reference_id, created_at)`, which serves the notification
    de-duplication check now that it keys on the event rather than on the
    recipient. Both are ordinary `CREATE INDEX`; on a large task or notification
    table they are the slowest part of this migration, though still fast by the
    standards of a maintenance window.
  - **Nothing else is touched.** `assigned_user_id` keeps its column, its foreign
    key and its behaviour, and every existing per-user assignment path is
    unchanged.

  **Downgrade** drops both columns, both composite foreign keys and both indexes,
  discarding any per-task team overrides that had been set. The tasks themselves,
  their assignees and their parent evidence items survive. As always, prefer
  restore-from-backup over a downgrade — see §4.

- **Internal vs external-contractor membership (migrations `orgmembertype1`
  and `invitemembertype1`).** This release records, per organisation, whether a
  member is permanent staff or an external contractor, so that "is any control
  owned by a contractor?" becomes a question the platform can answer. Both
  migrations are applied by phase 4 of the upgrade like any other; there is
  nothing extra to run.

  They are **schema-only and additive**:

  - `organization_members` gains `member_type VARCHAR(30) NOT NULL DEFAULT
    'internal'`, a CHECK restricting it to `'internal'` or
    `'external_contractor'`, and an index on `(organization_id, member_type)`.
  - `organization_invites` gains the same column and the same CHECK, so an
    invitation carries the employment type through to the membership it
    creates. It gets no index — invites are fetched by token or by
    organisation, never filtered by employment type.
  - **Every existing member becomes `internal`.** That is the safe default, not
    a judgement: the platform has no basis for inferring who is a contractor,
    so nothing is guessed. An admin marks contractors explicitly afterwards,
    from the member's row in User Management or on the invitation.
  - **Nothing else is touched.** Existing roles, assignments and permissions are
    unchanged, and `consultant_invites` is deliberately left alone — the
    consultant portal is a separate relationship from organisation membership.
  - **`member_type` grants and removes nothing.** Access control remains on the
    organisation role (admin / editor / viewer). Marking somebody a contractor
    makes their status *visible* — a badge wherever they appear as an owner,
    assignee or team member, and a filter on the controls and evidence lists —
    it does not restrict what they can do. Only an org **admin** may set it.

  Expect both migrations to be fast: two `ALTER TABLE ... ADD COLUMN` with a
  constant default, which Postgres 11+ applies without rewriting the table,
  regardless of how many members you have.

  **Downgrade** drops both columns and their constraints, discarding which
  members were marked as contractors. Roles and memberships themselves survive.
  As always, prefer restore-from-backup over a downgrade — see §4.

- **Team assignment of controls and evidence (migration `ctrlteamassign1`).**
  This release lets the teams created by `teamsfunctions1` actually own things:
  a scoped control or an evidence item can be assigned to one or more teams,
  exactly one of which is marked accountable. The migration chains from
  `teamsfunctions1` and is applied by phase 4 of the upgrade like any other —
  there is nothing extra to run.

  It is **schema-only and additive**:

  - It creates two new tables, `control_team_assignments` and
    `evidence_team_assignments`. Each attaches a team to one scoped control or
    one evidence tracking record, records who assigned it and when, and carries
    an `is_accountable` flag.
  - **At most one accountable team per item, enforced by the database.** A
    partial unique index rejects a second accountable team on the same control
    or evidence item. *At most*, not *exactly*: an item nobody has assigned yet
    has no accountable team, which is the state every control and evidence item
    is in until somebody picks one, so nothing here requires a row to exist. An
    item with owning teams but none accountable is legal, and shows a warning
    badge in the UI rather than blocking anything.
  - **No assignments are created for you.** The migration writes no rows and
    reads no existing owner column. `scoped_controls.owner`,
    `scoped_controls.assigned_to` and `evidence_tracking.owner` hold free text
    that was never validated against anything — team names alongside person
    names, `"TBD"`, blanks and per-organisation spellings — and deriving
    assignments from them would write junk into every tenant at once with no way
    back. Recovering those labels is a separate, operator-run, dry-run-first
    reconciliation in a later release. Until an admin assigns teams, every
    control and evidence item is exactly as it was.
  - **Every existing per-user assignment keeps working unchanged.**
    `assigned_user_id`, `owner_user_id` and the existing `assignments` table are
    untouched. Team ownership is a second, durable axis alongside them, not a
    replacement.
  - **Teams still grant no permissions.** Access control remains on the
    organisation role (admin / editor / viewer). Assigning a team to a control
    describes who answers for it; it changes nobody's access.
  - **One thing here is not instantaneous.** The two composite foreign keys need
    composite targets, so the migration adds `uq_scoped_controls_org_id` and
    `uq_evidence_tracking_org_id` — unique constraints on
    `(organization_id, id)` that exist purely to be foreign-key targets. Each
    builds a unique index over an existing table and therefore takes a brief
    `ACCESS EXCLUSIVE` lock. On a deployment with a large control set that is a
    short write stall during the migration, not a no-op. It is still measured in
    seconds, and phase 4 of the upgrade runs with the application stopped, so
    nothing is contending for the lock.

  **Downgrade** drops both tables and everything in them — every team assignment
  and every record of which team was accountable — then drops the two unique
  constraints it added. Teams, functions and team membership survive. As always,
  prefer restore-from-backup over a downgrade — see §4.

- **Teams may serve multiple functions (migration `teamfunctions2`).** The new
  `team_functions` join table is backfilled from every team's existing primary
  `function_id`, so all current teams retain exactly their present alignment.
  The primary column remains in place for old API clients and guarantees that a
  team always has at least one function. The migration is additive and does not
  infer or create any new alignment. Downgrade removes only the plural mappings;
  each team's primary function survives.

- **New tables for teams and functions (migration `teamsfunctions1`).** This
  release adds organisation structure: business **functions**, **teams**, and
  **team members**. The migration chains from `auditappendonly1` and is applied
  by phase 4 of the upgrade like any other — there is nothing extra to run.

  It is **schema-only and additive**:

  - It creates three new tables — `functions`, `teams` and `team_members` — and
    seeds `functions` with the fourteen platform-defined business functions
    (Governance, Risk & Compliance; Security Operations; Legal; and so on). The
    seeded rows use fixed ids, so a given function has the same id in every
    environment.
  - **No existing table or row is altered.** No column is added to, renamed in,
    or dropped from anything you already have.
  - **No teams are created for you.** Nothing is inferred from existing owner
    fields; every organisation starts with zero teams and an admin creates them
    when they are ready. An organisation that never creates a team is entirely
    unaffected by this release.
  - **Every existing per-user assignment keeps working unchanged.** Teams do not
    replace assignment and they grant no permissions — access control remains on
    the organisation role (admin / editor / viewer).

  Expect the migration to be fast: three empty tables and fourteen seed rows,
  regardless of how large your database is.

  **Downgrade** drops the three tables (`team_members`, `teams`, `functions`)
  and everything in them. As always, prefer restore-from-backup over a downgrade
  — see §4.

- **Postgres host publish is now loopback-only.** `docker-compose.yml` publishes
  postgres on `127.0.0.1:5432` instead of all interfaces — the app is unaffected
  (it uses the internal Docker network), but if you connected to the database
  **from another machine** (psql/DBeaver/external backup jobs pointed at
  `<docker-host>:5432`), those connections will now be refused. To restore remote
  access deliberately, override the bind address in a
  `docker-compose.override.yml` overlay (see §6). Host-side ports are also now
  remappable via `.env` (`BACKEND_PORT`, `FRONTEND_PORT`, `MINIO_PORT`,
  `MINIO_CONSOLE_PORT`, `POSTGRES_PORT`, `KEYCLOAK_PORT`); defaults are unchanged.

---

## 4. Rolling back

If an upgrade fails a verification check, the tool rolls back automatically. To
roll back manually to a specific backup set:

```bash
# The timestamp is the prefix of the files under ./backups/, e.g. 20260801_143000
scripts/upgrade.sh --rollback 20260801_143000
```

Rollback restores **both** stores. Postgres is restored **into a fresh database
and swapped in only after the restore proves good**, so your current (failed)
state is never destroyed mid-restore — it is set aside as `<db>_failed` for
inspection, and you can drop it once satisfied. The MinIO evidence volume is
restored from the phase-2 tar, and the code is checked out back to the
pre-upgrade commit.

> Database rollback is **restore-from-backup**, not `alembic downgrade` —
> downgrade migrations are not trusted for a compliance dataset.

---

## 5. Air-gapped / offline upgrades

The same script works offline; you supply the inputs out-of-band.

1. **Get the code across, including the tag ref.** A `git bundle` must include
   the tag, or the checkout in phase 3 will fail:

   ```bash
   # On a connected machine:
   git bundle create scf-v0.9.0.bundle v0.8.0..v0.9.0 refs/tags/v0.9.0

   # On the air-gapped host, from the repo root:
   git fetch ./scf-v0.9.0.bundle 'refs/tags/*:refs/tags/*'
   ```

   (A full release tarball is an alternative to the bundle.)

2. **Supply the manifest locally** instead of fetching it from GitHub:

   ```bash
   scripts/upgrade.sh v0.9.0 --manifest ./upgrade-manifest.json
   ```

3. **Pre-cache base images.** `docker compose up --build` re-resolves base
   images (e.g. MinIO); make sure the exact tags in `docker-compose.yml` are
   already present in the host's Docker cache, or the rebuild will fail with no
   registry to pull from.

Update discovery (the in-app badge) is outbound-only to GitHub and can be turned
off entirely; on an air-gapped install it simply reports "disabled".

---

## 6. Keeping local changes

If you have edited tracked files (`docker-compose.yml`, source), the preflight
**stops** and lists them rather than clobbering them. Keep local changes as a
thin overlay instead:

- a `docker-compose.override.yml` for compose tweaks, and
- your `.env` for configuration (it is untracked and survives upgrades).

That way upgrades never conflict with your customisations.
