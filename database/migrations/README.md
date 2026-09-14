# Database migrations

Alembic migration scripts for ADG. They live outside `backend/` so that schema history is
reviewable on its own.

Run migrations from the `backend` directory (it holds `alembic.ini` and is where the `app`
package is importable):

```powershell
cd C:\code\adg\backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic revision -m "add identity tables"
```

The connection string comes from `ADG_DATABASE_URL` (see `.env.example`), never from
`alembic.ini`.
