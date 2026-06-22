## CG SCF Webclient

Client-side UI to browse SCF controls, audit artifacts, and framework mappings. Development mode includes file-write capabilities for control scoping.

### Features

#### Dashboard Tab
- **Control Scoping Overview:** Total controls, in-scope count, breakdown by domain and framework
- **Implementation Status:** Status breakdown, priority distribution, implementation progress
- **Control Maturity:** Average maturity level across all selected controls with detailed breakdown
- **Evidence Tracking:** Tracked evidence count, progress by evidence domain and team
- **Framework Tracking:** Comprehensive coverage and implementation status for each compliance framework
  - Total controls mapped per framework
  - In-scope control count and coverage percentage
  - Implementation status breakdown (Implemented, In Progress, At Risk, Not Started)
  - Framework readiness indicator (Excellent, Good, Fair, Needs Work)
  - Grid layout with logo placeholders
- **Visual Progress Bars:** Quick view of completion percentages
- **Empty State:** Import/export buttons when no data is loaded

#### Control Library Tab
- Control list with search
- Details panel with description, guidance, testing
- Audit artifacts resolved from ERL
- Framework mappings grouped by framework
- Basic graph view (React Flow) connecting a control to its artifacts and mappings

#### Control Scoping Tab
- Select controls based on framework requirements
- Track implementation status and ownership
- Manage control priorities and timelines
- Link controls to related documentation (policies, procedures, etc.)
- Auto-save to file in development mode

#### Evidence Review Tab
- Review audit artifacts and evidence requirements for selected controls
- Mark evidence as "actively tracked" with explicit checkbox
- See which control(s) require each evidence item (many-to-many relationships)
- Click to jump between related controls for context
- Track evidence collection methods and systems
- Document owner and collection frequency
- Add comments and notes about evidence collection
- Get accurate metrics: X/Y evidence items are being tracked
- Auto-save to file in development mode

### Data Architecture

This application uses two separate data systems:

#### 1. SCF Control Library (Database-Backed)

**Source Files:** `/webclient/public/data/`
- `control_guidance.json` - SCF control definitions (1,451 controls)
- `domains.json` - SCF domain definitions (33 domains)
- `erl.json` - Evidence Request List entries (272 items)
- `assessment_objectives.json` - Assessment objectives (5,736 objectives)
- `frameworks.json` - Framework mapping definitions

**How it works:**
- On application startup, the backend seeds these JSON files into PostgreSQL
- Database tables: `scf_catalog_controls`, `scf_catalog_domains`, `scf_catalog_evidence`, `scf_catalog_assessment_objectives`
- API serves catalog data from the database
- Seeding is idempotent - only runs if tables are empty

**To update SCF data:**
1. Edit the JSON files in `/webclient/public/data/`
2. Clear the database (or drop the catalog tables)
3. Restart the application to trigger re-seeding

#### 2. Application Data (Database-Backed)

**Storage:** PostgreSQL database via REST API

This contains **user-specific application state** (not part of the SCF library):
- Selected controls for your organization
- Implementation status and ownership
- Priorities, dates, and notes
- Evidence collection tracking (method, system, owner, frequency, comments)

**Why separate from SCF source?**
- SCF library is read-only reference data from Compliance Forward's Secure Controls Framework
- Scoping data is your organization's decisions and implementation tracking
- Prevents accidental modification of the canonical SCF library
- Clear separation: library vs. application state

**How it works:**
1. Edit control scoping in the UI
2. Changes are saved immediately to the PostgreSQL database via REST API
3. All users see updates within 30 seconds (auto-sync)
4. Database provides ACID transactions and multi-user support

**Multi-User Collaboration:**
- Changes sync automatically across users
- No merge conflicts or file locking issues
- Backup and restore using standard PostgreSQL tools
- See `MULTI_USER_SETUP.md` for collaboration details

**Legacy Note:** Earlier versions used file-based storage (`scoped_controls.json`). The migration to PostgreSQL provides better scalability and team collaboration. See `MIGRATION_SUMMARY.md` for migration details.

### Scripts
- `npm run dev` - starts the dev server (will copy data first)
- `npm run build` - builds the app (will copy data first)
- `npm run preview` - previews the built app

Note: Please start/stop the server yourself when you're ready.

### Configuration

Create a `webclient/.env` file to configure the application:

#### Branding
- `VITE_APP_LOGO` - Path to your logo image (default: `/cropped-Logo-301x101.webp`)
  - Can be a path relative to `public/` directory (e.g., `/my-logo.png`)
  - Or a full URL (e.g., `https://example.com/logo.png`)
  - Set to empty string to hide the logo
- `VITE_APP_TITLE` - Application title displayed in header (default: `SCF Controls Platform`)

#### API Configuration
- `VITE_API_URL` - Backend API URL (default: `http://localhost:8000/api`)
- `VITE_API_KEY` - API key for authentication (required if Google auth is disabled)
- `VITE_GOOGLE_AUTH_ENABLED` - Enable Google OAuth authentication (default: `false`)
- `VITE_GOOGLE_CLIENT_ID` - Google OAuth client ID (required if Google auth is enabled)
- `VITE_DEBUG_API` - Enable API debug logging (default: `false`)

**Example `.env` file:**
```env
VITE_APP_LOGO=/custom-logo.png
VITE_APP_TITLE=My Compliance Platform
VITE_API_URL=http://localhost:8000/api
VITE_GOOGLE_AUTH_ENABLED=true
VITE_GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
```

### Folder structure
```
webclient/
  public/          # static assets (data will be copied here)
  src/             # React app
  scripts/         # data copy script
```
