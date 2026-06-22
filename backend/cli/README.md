# Platform Admin CLI Toolkit

Command-line interface for platform-level administrative operations. These tools provide direct database access for managing users and organisations across the entire platform.

## Quick Start (Docker)

Run commands from your host machine using `docker compose exec`:

```bash
# Show help
docker compose exec backend python -m cli.admin --help

# Initial setup (run this first on a fresh deployment!)
docker compose exec backend python -m cli.admin setup

# Show platform statistics
docker compose exec backend python -m cli.admin stats

# List all users
docker compose exec backend python -m cli.admin list-users

# List platform admins only
docker compose exec backend python -m cli.admin list-admins

# List all organisations
docker compose exec backend python -m cli.admin list-orgs
```

## Commands

### Initial Setup

#### `setup`
**Run this first on any fresh deployment!** Creates the Default Organization required for user auto-provisioning.

```bash
# Create Default Organization (required for first login)
docker compose exec backend python -m cli.admin setup

# Custom organization name
docker compose exec backend python -m cli.admin setup --name "My Company"

# Preview without making changes
docker compose exec backend python -m cli.admin setup --dry-run
```

After running setup:
1. Sign in via Google OAuth
2. Your user will be auto-created and linked to the Default Organization
3. Run `grant-admin --email your@email.com` to make yourself platform admin

### User Management

#### `list-users`
List all users in the platform.

```bash
docker compose exec backend python -m cli.admin list-users
docker compose exec backend python -m cli.admin list-users --admins-only
```

#### `list-admins`
List all platform administrators.

```bash
docker compose exec backend python -m cli.admin list-admins
```

#### `grant-admin`
Grant platform admin privileges to a user.

```bash
# By email
docker compose exec backend python -m cli.admin grant-admin --email admin@example.com

# By user ID
docker compose exec backend python -m cli.admin grant-admin --user-id 123e4567-e89b-12d3-a456-426614174000

# Preview without making changes
docker compose exec backend python -m cli.admin grant-admin --email admin@example.com --dry-run
```

#### `revoke-admin`
Revoke platform admin privileges from a user.

```bash
docker compose exec backend python -m cli.admin revoke-admin --email admin@example.com
docker compose exec backend python -m cli.admin revoke-admin --user-id 123e4567-e89b-12d3-a456-426614174000 --dry-run
```

### Consultant Management

#### `grant-consultant`
Grant consultant access to a user. This is an **API-based command** that calls the platform admin API, so it requires `--base-url` and `--api-key`.

```bash
# By email
docker compose exec backend python -m cli.admin grant-consultant \
  --email consultant@example.com \
  --base-url https://eu.scfcontrolsplatform.com \
  --api-key YOUR_API_KEY

# With optional company name and client limit
docker compose exec backend python -m cli.admin grant-consultant \
  --email consultant@example.com \
  --base-url https://eu.scfcontrolsplatform.com \
  --api-key YOUR_API_KEY \
  --company-name "Acme GRC Consulting" \
  --max-clients 10

# By user ID
docker compose exec backend python -m cli.admin grant-consultant \
  --user-id 123e4567-e89b-12d3-a456-426614174000 \
  --base-url https://eu.scfcontrolsplatform.com \
  --api-key YOUR_API_KEY

# Preview without making changes
docker compose exec backend python -m cli.admin grant-consultant \
  --email consultant@example.com \
  --base-url https://eu.scfcontrolsplatform.com \
  --api-key YOUR_API_KEY \
  --dry-run
```

#### `revoke-consultant`
Revoke consultant access from a user (deactivates profile, preserves client history). Also **API-based**.

```bash
docker compose exec backend python -m cli.admin revoke-consultant \
  --email consultant@example.com \
  --base-url https://eu.scfcontrolsplatform.com \
  --api-key YOUR_API_KEY

docker compose exec backend python -m cli.admin revoke-consultant \
  --user-id 123e4567-e89b-12d3-a456-426614174000 \
  --base-url https://eu.scfcontrolsplatform.com \
  --api-key YOUR_API_KEY \
  --dry-run
```

> **Note:** Unlike `grant-admin`/`revoke-admin` which use direct database access, the consultant commands use the admin REST API. This means the platform must be running and accessible at the given `--base-url`.

### User Deletion

#### `delete-user`
Delete a user and all their associated data (memberships, assignments, comments, notifications).

```bash
# Preview what would be deleted
docker compose exec backend python -m cli.admin delete-user --email user@example.com

# Actually delete (requires --confirm)
docker compose exec backend python -m cli.admin delete-user --email user@example.com --confirm

# Dry run with confirmation flag shows what would happen
docker compose exec backend python -m cli.admin delete-user --email user@example.com --confirm --dry-run
```

### Organisation Management

#### `list-orgs`
List all organisations with member counts.

```bash
docker compose exec backend python -m cli.admin list-orgs
```

#### `delete-org`
Delete an organisation and all its data (controls, evidence, memberships).

```bash
# Preview what would be deleted
docker compose exec backend python -m cli.admin delete-org --slug my-organisation

# Actually delete (requires --confirm)
docker compose exec backend python -m cli.admin delete-org --slug my-organisation --confirm

# By organisation ID
docker compose exec backend python -m cli.admin delete-org --org-id 123e4567-e89b-12d3-a456-426614174000 --confirm
```

### Platform Statistics

#### `stats`
Show platform-wide statistics including user counts, organisation counts, and activity metrics.

```bash
docker compose exec backend python -m cli.admin stats
```

Output includes:
- Total users and platform admins
- Total organisations
- Total scoped controls and evidence items
- Users active in last 30 days
- Organisations created in last 30 days

## Safety Features

### Dry Run Mode
All destructive operations support `--dry-run` to preview changes without making them:

```bash
docker compose exec backend python -m cli.admin grant-admin --email admin@example.com --dry-run
docker compose exec backend python -m cli.admin delete-user --email user@example.com --confirm --dry-run
```

### Confirmation Required
Destructive operations (`delete-user`, `delete-org`) require the `--confirm` flag:

```bash
# This will NOT delete - shows preview only
docker compose exec backend python -m cli.admin delete-user --email user@example.com

# This WILL delete
docker compose exec backend python -m cli.admin delete-user --email user@example.com --confirm
```

## Examples

### Initial Platform Setup

```bash
# Check current state
docker compose exec backend python -m cli.admin stats

# Grant admin to first user
docker compose exec backend python -m cli.admin grant-admin --email founder@company.com

# Verify
docker compose exec backend python -m cli.admin list-admins
```

### User Offboarding

```bash
# Check user exists and their memberships
docker compose exec backend python -m cli.admin list-users | grep "user@example.com"

# Preview deletion
docker compose exec backend python -m cli.admin delete-user --email user@example.com

# Execute deletion
docker compose exec backend python -m cli.admin delete-user --email user@example.com --confirm
```

### Organisation Cleanup

```bash
# List organisations
docker compose exec backend python -m cli.admin list-orgs

# Preview deletion
docker compose exec backend python -m cli.admin delete-org --slug test-organisation

# Execute deletion
docker compose exec backend python -m cli.admin delete-org --slug test-organisation --confirm
```

## Mock Data Seeding

To populate the platform with realistic demo data, use the separate mock data script:

```bash
# Seed all mock data (run from project root)
python scripts/seed_mock_data.py

# Preview without changes
python scripts/seed_mock_data.py --dry-run

# Seed a single category
python scripts/seed_mock_data.py --step maturity
```

See `scripts/README.md` for full documentation.

---

## API Alternative

All CLI operations are also available via REST API at `/api/admin/`. See the API documentation for programmatic access.

| CLI Command | API Endpoint |
|------------|--------------|
| `list-users` | `GET /api/admin/users` |
| `list-admins` | `GET /api/admin/users?admins_only=true` |
| `grant-admin` | `POST /api/admin/users/grant-admin` |
| `revoke-admin` | `POST /api/admin/users/revoke-admin` |
| `grant-consultant` | `POST /api/admin/users/{id}/grant-consultant` |
| `revoke-consultant` | `POST /api/admin/users/{id}/revoke-consultant` |
| `delete-user` | `DELETE /api/admin/users/{id}?confirm=true` |
| `list-orgs` | `GET /api/admin/organizations` |
| `delete-org` | `DELETE /api/admin/organizations/{id}?confirm=true` |
| `stats` | `GET /api/admin/stats` |

## Local Development (Without Docker)

If running locally without Docker:

### Prerequisites

- Python 3.11+
- Database connection configured via `DATABASE_URL` environment variable
- Run from the `backend/` directory

### Commands

```bash
cd backend

# Set database connection
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/dbname"

# Run commands
python -m cli.admin stats
python -m cli.admin list-users
python -m cli.admin list-orgs
```

## Troubleshooting

### Container Not Running
Ensure the backend container is running:
```bash
docker compose ps
docker compose up -d backend
```

### Database Connection (Local)
Ensure `DATABASE_URL` is set correctly:
```bash
export DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/dbname"
```

### Import Errors (Local)
Run from the `backend/` directory to ensure proper module resolution:
```bash
cd backend
python -m cli.admin stats
```

### Permission Denied
CLI operations bypass API authentication but require direct database access. Ensure your database credentials have appropriate permissions.
