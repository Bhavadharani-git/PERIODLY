# PERIODLY security and deployment boundary

## Production controls in this release

- Production startup rejects SQLite, weak/placeholder database passwords, missing/short admin API keys, wildcard CORS, and non-HTTPS cross-origin CORS entries.
- Production disables interactive OpenAPI documentation.
- All `/api/*` routes except the health probe require `X-API-Key`.
- The browser requests the shared key when first challenged and stores it in tab-scoped session storage, not in source code or local storage.
- The default container port is bound to host loopback only. Place it behind an HTTPS reverse proxy / managed HTTPS platform.
- Git and Docker ignore local databases, virtual environments, `.env` files, backups and exports.

## Important limitation

This release implements a single shared administrator key. It does **not** implement per-user login, roles, password reset, school tenant authorization, audit attribution, session revocation or per-user activity logs. Do not run a public multi-school SaaS service with this model. Add full identity and tenant authorization before that use case.

## Operational requirements

1. Use HTTPS end-to-end at the public boundary.
2. Use unique, randomly generated database and API secrets. Never commit `.env`.
3. Keep PostgreSQL on a private network; do not publish its port.
4. Restrict reverse-proxy access to intended school administrators.
5. Back up PostgreSQL and test restores before importing production school records.
6. Replace demo staff data and verify teacher qualifications, availability, room capacities and weekly requirements.
7. Review generated timetables before publishing them to teachers or students.
8. Rotate the shared API key if an administrator leaves or the key may have been exposed.
