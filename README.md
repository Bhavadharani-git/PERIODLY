# PERIODLY — Intelligent School Timetable Scheduler

PERIODLY is a school-wide timetable scheduler using Google OR-Tools CP-SAT constraint optimization.
It is separate from SCHEDULA.

## Verified local capabilities

- School-wide timetable optimization across multiple classes.
- Hard rules for class/teacher/room slot collisions, qualifications, working-day and explicit availability,
  teacher weekly maximums, weekly subject requirements, locked lessons, room capacity and consecutive
  double-period blocks where configured.
- Soft objectives for teacher/subject preferences, subject spread, daily load balance, teacher workload
  balance and internal class-day gap minimization.
- Database persistence of generated lessons and Excel export.

The CP-SAT engine is a constraint-optimization engine, not a trained machine-learning model or an LLM.
Its OPTIMAL status applies to the implemented model/objective for that specific run.

## Local Windows setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# For a brand-new empty database only:
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for local API checks. The frontend can be opened directly from
`frontend/index.html`, or served by the backend at `http://127.0.0.1:8000/`.

The local default database is `backend/periodly.db`. Keep that file backed up and never commit it.
For an existing database created by an earlier PERIODLY version, do not run `alembic upgrade head` against it
without first checking its schema. Back it up and use `alembic stamp head` only after confirming the existing
tables and columns already match the release baseline. The API retains `create_all` for adding missing tables,
but Alembic is the tracked schema-upgrade mechanism from this release onward.

## Production deployment with Docker Compose

Production mode runs `alembic upgrade head` before starting the API, and requires PostgreSQL and a random API key of at least 32 characters. The frontend is served
by the API container on the same origin, so the API URL is inferred automatically. Production API docs are
disabled. All `/api/*` endpoints other than `/api/health` require the `X-API-Key` header; the browser prompts
for the key and keeps it in tab-scoped session storage.

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
# Run the command again for a DIFFERENT value. Edit .env and replace both placeholders:
# one generated value for POSTGRES_PASSWORD and another for PERIODLY_API_KEY.
notepad .env
docker compose up -d --build
docker compose ps
```

Browse to `http://localhost:8000/` for a local container smoke test. For internet use, put the service behind
a trusted HTTPS reverse proxy or hosting platform with TLS; do not expose the container's port directly.
`CORS_ORIGINS` should remain empty for same-origin hosting. If a separate frontend origin is used, specify
only its exact HTTPS origin(s), comma-separated.

### Move the existing local SQLite school data into production PostgreSQL

Do this only after a verified backup. Stop the local PERIODLY backend first, then create a consistent SQLite
backup using Python's SQLite backup API (replace the path with the actual local project path):

```powershell
python -c "import sqlite3; src=sqlite3.connect(r'D:\PERIODLY_FastTrack_v3_ConfigModules\PERIODLY\backend\periodly.db'); dst=sqlite3.connect(r'D:\PERIODLY_FastTrack_v3_ConfigModules\PERIODLY\backend\periodly_migration_source.db'); src.backup(dst); dst.close(); src.close()"
```

After `docker compose up -d --build`, copy that backup into the app container and import it into the empty
PostgreSQL database. The importer preserves primary IDs and foreign-key order, skips tables absent from an older
SQLite source, and refuses to copy into a target that already contains application rows:

```powershell
docker compose cp .\backend\periodly_migration_source.db app:/tmp/periodly_migration_source.db
docker compose exec app python -m app.migrate_database --source-url sqlite:////tmp/periodly_migration_source.db
```

Check the importer row counts, then open PERIODLY and verify the expected schools, classes, teachers, subjects,
weekly requirements and timetable entries before allowing normal use. Do not run the importer twice into a
non-empty target; it deliberately refuses that operation.

**Authentication limitation:** the production API key is a single shared administrator gate, not per-user
accounts, roles, audit attribution or school-level tenant isolation. It is suitable only for a controlled
single-school administrator deployment behind HTTPS. A public multi-school SaaS deployment requires full
user authentication, role-based authorization, per-school access enforcement and audit logging before launch.

## Database backups

```powershell
docker compose exec -T db sh -c "pg_dump -U periodly -d periodly > /tmp/periodly-backup.sql"
docker compose cp db:/tmp/periodly-backup.sql .\periodly-backup.sql
```

Restore only into an empty, verified restore target; restoring can overwrite database content:

```powershell
docker compose cp .\periodly-backup.sql db:/tmp/periodly-restore.sql
docker compose exec -T db psql -U periodly -d periodly -f /tmp/periodly-restore.sql
```

## Automated checks

```powershell
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q app
```

## Git release hygiene

The root `.gitignore` excludes virtual environments, `.env` secrets, SQLite files, backups, exports and caches.
Review `git status --short` before staging. Never add the school's database or secrets to Git.

Example first push (replace the URL with the actual empty GitHub/GitLab repository URL):

```powershell
git init
git branch -M main
git add .
git status --short
git diff --cached --name-only
# Confirm no .env, *.db, backup, export or .venv path is staged.
git commit -m "Prepare PERIODLY CP-SAT release"
git remote add origin https://github.com/YOUR-ORG/YOUR-REPOSITORY.git
git push -u origin main
```

A guarded helper is included at `scripts/Push-To-Git.ps1`. Run it from the project root after checking the staged
file list; it refuses to push databases, `.env` secrets, virtual environments, backups and generated exports:

```powershell
.\scripts\Push-To-Git.ps1 -RemoteUrl "https://github.com/YOUR-ORG/YOUR-REPOSITORY.git"
```

If this project already has a Git repository or remote, inspect `git remote -v` and `git status` first. The helper
will stop if the existing `origin` differs from the URL you supply. Never paste an access token into the repository or chat.

## Important operational checks before a school launch

- Replace all demo teacher records with the real roster and qualifications.
- Configure actual teacher availability, teacher preferences and subject profiles.
- Configure rooms and capacities if rooms are mandatory in the school's policy.
- Test locked lessons and conflict diagnostics using representative real data.
- Validate every class/subject weekly requirement and manually review the published timetable.
- Confirm backup and restore in the actual hosting environment.
