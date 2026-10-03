# Development

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Node.js 22 and npm
- Docker (only to build the image locally)

## First setup

```sh
cd backend && uv sync && cd ..
cd frontend && npm install && cd ..
pipx install pre-commit && pre-commit install   # checks before each commit
```

## Running locally

Backend (API on http://localhost:8000, data in `./data`):

```sh
cd backend
DATA_DIR=../data uv run uvicorn app.main:app --reload
```

Web UI with hot reload (http://localhost:5173, API calls are forwarded to the
backend):

```sh
cd frontend
npm run dev
```

`npm run build` writes the UI to `backend/static/`, where the backend serves it
on port 8000 like in production.

## Checks

These run before each commit (pre-commit) and on every pull request (CI):

```sh
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd frontend && npm run lint && npm run build
```

## Database changes

After changing models, create a migration and review it:

```sh
cd backend
DATA_DIR=../data uv run alembic revision --autogenerate -m "describe the change"
```

Migrations run automatically when the app starts.

## Docker image

```sh
docker build -t fuel-tracker:local --build-arg APP_VERSION=0.0.0-test+local .
docker run --rm -p 8000:8000 -v fuel-tracker-data:/data fuel-tracker:local
```

## Testing against a real LubeLogger

The tests use a fake LubeLogger by default. One extra test talks to a real one
(it is skipped otherwise). Use a throwaway LubeLogger, as the test creates
fuel records in it:

```sh
LUBELOGGER_TEST_URL=http://localhost:8080 uv run pytest tests/test_lubelogger_integration.py
```

Create a vehicle first (`POST /api/vehicles/add`). To run LubeLogger from its
source, build it with the .NET SDK (`dotnet publish -c Release`) and start it
with `ASPNETCORE_URLS=http://127.0.0.1:8080 dotnet CarCareTracker.dll`.

## Testing against a real mail server

Likewise, `tests/test_imap_integration.py` talks to a real IMAP server and is
skipped unless `IMAP_TEST_HOST` is set. [GreenMail](https://greenmail-mail-test.github.io/greenmail/)
is a throwaway server for it; the command is in the test file's docstring.
