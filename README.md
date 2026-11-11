# PRSense

AI Pull Request Risk & Code Review Intelligence Platform.

> **Status: Phase 1 (foundation) of the MVP.** Only the backend foundation is implemented:
> custom user model, health endpoint, API error envelope, settings, and tests. GitHub integration,
> analysis, ML, RAG, LLM review, and the frontend are not built yet. See `docs/architecture.md`.

PRSense is an assistant for developers. It never approves, rejects, merges, or modifies
Pull Requests.

## Backend setup

Requirements: Python 3.11 or 3.12, PostgreSQL 14+ (pgvector is needed from Phase 5).

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env               # then edit values; never commit .env
python manage.py migrate
python manage.py test tests
python manage.py runserver
```

Health check: `GET http://127.0.0.1:8000/api/health/` returns `200` with `{"status": "ok"}`
when the database is reachable, and `503` otherwise.

## Lint

```bash
cd backend && ruff check .
```

## Configuration

All settings come from environment variables; see `backend/.env.example`.
Production requires `DJANGO_SECRET_KEY` and `DJANGO_DEBUG=False`.

## Documentation

- `docs/architecture.md`: architecture decisions and deviations.
