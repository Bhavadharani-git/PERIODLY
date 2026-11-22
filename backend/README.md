# PERIODLY backend

Local Windows development:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# For a new empty database; existing databases need schema verification and may need `alembic stamp head`.
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

Local API docs: http://127.0.0.1:8000/docs
Health: http://127.0.0.1:8000/api/health

Production configuration is environment driven. It requires PostgreSQL in `DATABASE_URL` and
`PERIODLY_API_KEY` with at least 32 characters. See the project root README and `.env.example`.
Never deploy with SQLite, wildcard CORS, or the development server's `--reload` flag.
