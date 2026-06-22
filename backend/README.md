# SCF Controls Platform Backend API

Python FastAPI backend for the SCF Controls Platform (formerly CG SCF Explorer), providing REST API endpoints for scoped controls and evidence tracking management.

> **Note**: As of v4.0.0, this platform has migrated from CCF (Common Controls Framework) to SCF (Secure Controls Framework) v4. The `ccf_id` field has been renamed to `scf_id` throughout.

## Overview

- **Framework**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL 15 with asyncpg
- **ORM**: SQLAlchemy 2.0 (async)
- **Validation**: Pydantic v2
- **Server**: Uvicorn with async support

## Project Structure

```
backend/
├── main.py                 # FastAPI application entry point
├── database.py             # Database connection and session management
├── models.py               # SQLAlchemy ORM models
├── schemas.py              # Pydantic schemas for API validation
├── requirements.txt        # Python dependencies
├── init-db.sql            # Database initialization script
├── api/                   # API endpoint modules
│   ├── __init__.py
│   ├── organizations.py   # Organization endpoints
│   ├── scoped_controls.py # Scoped controls CRUD
│   └── evidence_tracking.py # Evidence tracking CRUD
└── data/                  # Static SCF data (mounted from data source)
    └── scf/               # control_guidance.json, erl.json, etc.
```

## API Endpoints

### Health & Status

- `GET /health` - Health check for Docker
- `GET /` - API information

### Organizations

- `GET /api/organizations` - List all organizations
- `GET /api/organizations/{org_id}` - Get organization details

### Scoped Controls

- `GET /api/organizations/{org_id}/scoped-controls` - List scoped controls
- `GET /api/organizations/{org_id}/scoped-controls/{scf_id}` - Get single control
- `POST /api/organizations/{org_id}/scoped-controls` - Create/update control (upsert)
- `PATCH /api/organizations/{org_id}/scoped-controls/{scf_id}` - Partial update
- `DELETE /api/organizations/{org_id}/scoped-controls/{scf_id}` - Delete control

### Evidence Tracking

- `GET /api/organizations/{org_id}/evidence-tracking` - List evidence tracking
- `GET /api/organizations/{org_id}/evidence-tracking/{evidence_id}` - Get single tracking
- `POST /api/organizations/{org_id}/evidence-tracking` - Create/update tracking (upsert)
- `PATCH /api/organizations/{org_id}/evidence-tracking/{evidence_id}` - Partial update

### Platform Admin

- `GET /api/admin/users` - List all users (platform admin only)
- `GET /api/admin/users/{user_id}` - Get user details
- `POST /api/admin/users/grant-admin` - Grant platform admin
- `POST /api/admin/users/revoke-admin` - Revoke platform admin
- `POST /api/admin/users/{user_id}/grant-consultant` - Grant consultant access
- `POST /api/admin/users/{user_id}/revoke-consultant` - Revoke consultant access
- `DELETE /api/admin/users/{user_id}` - Delete user
- `GET /api/admin/organizations` - List all organisations
- `DELETE /api/admin/organizations/{org_id}` - Delete organisation
- `GET /api/admin/stats` - Platform statistics

### Provisioning & Subscriptions

- `GET /api/provisioning/subscription` - Get current user's subscription
- `GET /api/provisioning/usage` - Get current usage vs limits
- `POST /api/provisioning/sync` - Sync subscription from marketing site webhook (API key auth)

### Consultant Portal

- `GET /api/consultant/check` - Check consultant access status
- `POST /api/consultant/register` - Register as consultant
- `GET /api/consultant/dashboard` - Consultant dashboard data
- `GET /api/consultant/clients` - List consultant's client organisations
- `POST /api/consultant/clients/invite` - Invite a client
- `GET /api/consultant/invites` - List pending invitations
- `DELETE /api/consultant/invites/{invite_id}` - Cancel invitation

### API Documentation

- `GET /docs` - Interactive Swagger UI
- `GET /redoc` - Alternative ReDoc UI
- `GET /openapi.json` - OpenAPI schema

## Database Models

### Organization
- **id** (UUID) - Primary key
- **name** (String) - Organization name
- **slug** (String) - URL-friendly identifier
- **created_at** (DateTime)
- **updated_at** (DateTime)

### ScopedControl
- **id** (UUID) - Primary key
- **organization_id** (UUID) - Foreign key
- **scf_id** (String) - SCF control identifier (e.g., "IAC-01", "NET-02")
- **selected** (Boolean) - Is control selected
- **implementation_status** (String) - Status (not_started, in_progress, implemented, etc.)
- **priority** (String) - Priority level
- **owner** (String) - Control owner
- **assigned_to** (String) - Assignee
- **maturity_level** (String) - Maturity level
- **target_date** (Date) - Target completion date
- **completion_date** (Date) - Actual completion date
- **implementation_notes** (Text) - Implementation notes
- **related_documentation** (JSONB) - Links and references
- **custom_fields** (JSONB) - Custom metadata
- **created_at**, **updated_at** (DateTime)

### EvidenceTracking
- **id** (UUID) - Primary key
- **organization_id** (UUID) - Foreign key
- **evidence_id** (String) - ERL evidence identifier
- **is_tracked** (Boolean) - Is evidence tracked
- **method_of_collection** (Text) - Collection method
- **collecting_system** (String) - System used for collection
- **owner** (String) - Evidence owner
- **frequency** (String) - Collection frequency
- **comments** (Text) - Additional comments
- **created_at**, **updated_at** (DateTime)

## Development Setup

### Prerequisites

- Python 3.11 or higher
- PostgreSQL 15 (or use Docker)
- pip or poetry

### Local Development

```bash
cd backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export DATABASE_URL=postgresql+asyncpg://odin:changeme@localhost:5432/odin_scf
export CORS_ORIGINS=http://localhost:5173,http://localhost:3000
export LOG_LEVEL=debug

# Run development server (with auto-reload)
uvicorn main:app --reload --port 8000
```

### With Docker Compose

```bash
# From project root
docker-compose up -d postgres backend

# View logs
docker-compose logs -f backend
```

## Environment Variables

Required environment variables:

```bash
# Database connection (PostgreSQL with asyncpg driver)
DATABASE_URL=postgresql+asyncpg://user:password@host:port/database

# CORS origins (comma-separated)
CORS_ORIGINS=http://localhost:3000,http://localhost:5173

# Log level (debug, info, warning, error, critical)
LOG_LEVEL=info

# Environment (development, production)
ENVIRONMENT=development
```

## Database Migrations

The database schema is initialized automatically on first run via `init-db.sql`.

For schema changes, you can:

1. **Manual SQL** - Execute SQL directly
2. **Alembic** - Use Alembic for versioned migrations (recommended for production)

### Setting up Alembic (Optional)

```bash
# Initialize Alembic
alembic init alembic

# Edit alembic.ini and set sqlalchemy.url
# Edit alembic/env.py to import your models

# Create migration
alembic revision --autogenerate -m "description"

# Apply migration
alembic upgrade head

# Rollback
alembic downgrade -1
```

## API Request/Response Examples

### Create/Update Scoped Control

**Request:**
```bash
POST /api/organizations/{org_id}/scoped-controls
Content-Type: application/json

{
  "scf_id": "AST-01",
  "selected": true,
  "implementation_status": "in_progress",
  "priority": "high",
  "owner": "John Doe",
  "maturity_level": "developing",
  "target_date": "2025-12-31",
  "implementation_notes": "Working on asset inventory system",
  "related_documentation": {
    "policy_link": "https://example.com/asset-policy"
  }
}
```

**Response:**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "organization_id": "987fcdeb-51d2-43e8-b123-456789abcdef",
  "scf_id": "AST-01",
  "selected": true,
  "implementation_status": "in_progress",
  "priority": "high",
  "owner": "John Doe",
  "assigned_to": null,
  "maturity_level": "developing",
  "target_date": "2025-12-31",
  "completion_date": null,
  "implementation_notes": "Working on asset inventory system",
  "related_documentation": {
    "policy_link": "https://example.com/asset-policy"
  },
  "custom_fields": null,
  "created_at": "2025-01-04T10:00:00Z",
  "updated_at": "2025-01-04T10:00:00Z"
}
```

### Get All Scoped Controls

**Request:**
```bash
GET /api/organizations/{org_id}/scoped-controls
```

**Response:**
```json
[
  {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "scf_id": "AST-01",
    "selected": true,
    "implementation_status": "in_progress",
    ...
  },
  {
    "id": "234e5678-e89b-12d3-a456-426614174001",
    "scf_id": "AST-02",
    "selected": false,
    ...
  }
]
```

## Error Handling

The API uses standard HTTP status codes:

- **200 OK** - Success
- **201 Created** - Resource created
- **404 Not Found** - Resource not found
- **422 Unprocessable Entity** - Validation error
- **500 Internal Server Error** - Server error

Error response format:
```json
{
  "success": false,
  "error": "Error message",
  "detail": "Detailed error information"
}
```

## Testing

### Manual Testing

Use the interactive API docs:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

Or use curl:
```bash
# Health check
curl http://localhost:8000/health

# Get organizations
curl http://localhost:8000/api/organizations

# Create scoped control
curl -X POST http://localhost:8000/api/organizations/{org_id}/scoped-controls \
  -H "Content-Type: application/json" \
  -d '{"scf_id": "AST-01", "selected": true}'
```

### Automated Testing

To add unit tests (example with pytest):

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Create tests/test_api.py
# Run tests
pytest tests/
```

## Performance Considerations

### Database Connection Pooling

SQLAlchemy connection pool is configured in `database.py`:
- **pool_size**: 5 connections
- **max_overflow**: 10 additional connections
- **pool_pre_ping**: Verify connections before use

Adjust based on your workload.

### Async Operations

All database operations are asynchronous for better performance:
- Uses `asyncpg` driver
- `AsyncSession` for database sessions
- `async/await` throughout

### Caching

Consider adding caching for:
- Organization lookups (rarely change)
- SCF static data (control guidance, ERL - rarely changes)

Use Redis or in-memory caching if needed.

## Security

### Input Validation

All input is validated using Pydantic schemas:
- Type checking
- String length limits
- Required/optional fields
- Custom validators

### SQL Injection Prevention

SQLAlchemy ORM prevents SQL injection:
- Parameterized queries
- No raw SQL (unless explicitly needed)

### CORS Configuration

CORS is configured to allow specific origins:
```python
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

Update this for production deployment.

### Production Checklist

- [ ] Change default database password
- [ ] Use HTTPS
- [ ] Add rate limiting
- [ ] Add authentication/authorization (if needed)
- [ ] Configure proper logging
- [ ] Set up monitoring
- [ ] Regular security updates

## Logging

Structured logging is configured in `main.py`:
- Timestamp, level, module, message
- Configurable log level via `LOG_LEVEL` env var
- Logs to stdout (captured by Docker)

## Monitoring

### Health Checks

```bash
# Simple health check
curl http://localhost:8000/health

# Response: {"status": "healthy", "service": "cg-scf-backend", "version": "1.0.0"}
```

### Database Monitoring

```bash
# Check database connections
docker-compose exec postgres psql -U odin -d odin_scf \
  -c "SELECT count(*) FROM pg_stat_activity"

# Check table sizes
docker-compose exec postgres psql -U odin -d odin_scf \
  -c "SELECT tablename, pg_size_pretty(pg_total_relation_size(tablename::text))
      FROM pg_tables WHERE schemaname = 'public'"
```

## Troubleshooting

### Backend Won't Start

Check logs:
```bash
docker-compose logs backend
```

Common issues:
- Database connection failed → Check `DATABASE_URL`
- Port already in use → Change port or stop conflicting service
- Module import errors → Rebuild container

### Database Connection Timeout

```bash
# Verify postgres is running
docker-compose ps postgres

# Check postgres logs
docker-compose logs postgres

# Test connection manually
docker-compose exec backend python -c "from database import engine; import asyncio; asyncio.run(engine.connect())"
```

### Slow Queries

Enable query logging:
```python
# In database.py
engine = create_async_engine(
    DATABASE_URL,
    echo=True,  # Log all SQL queries
    ...
)
```

Check for missing indexes:
```sql
-- Find slow queries
SELECT query, calls, total_time, mean_time
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;
```

## Contributing

When contributing to the backend:

1. Follow PEP 8 style guidelines
2. Add type hints to all functions
3. Write docstrings for modules, classes, and functions
4. Add validation to Pydantic schemas
5. Test API endpoints manually or with automated tests
6. Update this README if adding new endpoints

## License

See main repository LICENSE file.

---

**Version**: 4.0.0 (SCF v4 Migration)
**Last Updated**: 2026-01-04
