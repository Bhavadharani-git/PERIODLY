# PERIODLY Release Readiness — 22 November 2026

## Verified from the user's live Windows environment

- OR-Tools imported successfully in the user's virtual environment.
- Single-class CP-SAT generation returned 28/28 allocated, zero unallocated, with both phases OPTIMAL.
- Two-class simultaneous CP-SAT generation returned 56/56 allocated, zero unallocated, both phases OPTIMAL and zero internal gaps reported.
- The user supplied the saved 56-row timetable API response. Manual review found 28 lessons per class, no duplicate class/day/period slots, no duplicate teacher/day/period slots, and teacher loads within the configured weekly maximum.
- The user's screenshots showed timetable data on the dashboard.

## Verified in this release build environment

- Automated tests: **12 passed, 1 skipped**.
- The skipped test module is the direct CP-SAT optimizer integration test because OR-Tools could not be installed in this build environment. The user's live Windows environment has already confirmed OR-Tools imports and the generator has returned live OPTIMAL results.
- Python compile check passed.
- Frontend inline JavaScript and `frontend/config.js` syntax checks passed.
- Docker Compose and GitHub Actions YAML parse checks passed.
- Alembic initial schema migration completed on a fresh SQLite test database.
- `alembic check` reported no model/schema drift on that migrated database.
- Production configuration tests reject missing/weak API keys, SQLite production databases, wildcard CORS, and non-origin HTTPS CORS values.
- API smoke tests verified database health, same-origin frontend serving, development Swagger, and the production API-key guard.
- SQLite-to-SQLite data-import tests preserved IDs/records and refused a non-empty target.
- The upload's `backend/periodly.db` was zero bytes; it is not the live school database and was not used as a live-data migration or solver test fixture.

## Not verified in this environment / required before public launch

- Docker image build and live PostgreSQL startup were not run because Docker is unavailable in this build environment.
- SQLite-to-PostgreSQL data migration was not run against the user's real database or a live PostgreSQL server.
- The user's live school has zero rooms, so room assignment/capacity/collision behavior was not demonstrated with real school data.
- The last live solve showed zero teacher profiles and zero subject profiles; the user's actual preference records and their effect remain to be verified.
- Locked lesson preservation and conflict reporting have automated test coverage included, but the OR-Tools integration tests were skipped here and the feature was not demonstrated in the user's live data.
- School-scale performance beyond the two-class test has not been measured.
- The production API key is one shared-admin gate, not per-user authentication, role-based authorization, school tenant isolation or audit attribution.
- HTTPS reverse-proxy setup, backup/restore and real deployment smoke tests remain hosting-specific.
- No Git remote push or cloud deployment was performed; the user has not supplied a repository URL, hosting target or credentials.

## Release recommendation

This is a **controlled single-school administrator pilot candidate**, not a certified public multi-school SaaS release. Keep the current local database backed up. Before using the Docker/PostgreSQL deployment, run the documented backup and importer against a copy of the real SQLite database, verify all record counts, test the actual admin key over HTTPS, and complete room/preference/locked-lesson checks. Public multi-school use requires proper user identity, RBAC, school-level authorization, audit logging and a security review.
