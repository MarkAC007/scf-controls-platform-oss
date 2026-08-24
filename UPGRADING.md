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
