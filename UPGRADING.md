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
  are confident in the new version.

### Version-specific notes

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
  > `docker-compose.prod-test.yml`, `POSTGRES_15` in `terraform-gcp/cloud-sql.tf`,
  > `engine_version = "15"` in `terraform-aws/rds.tf`, and PostgreSQL 15 in both
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
