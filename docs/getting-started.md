# Getting Started

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) — `pip install uv`
- Docker + Docker Compose (recommended for local dev)
- OpenAI API key, or Alibaba Cloud Model Studio API key when using Qwen
- Langfuse account (optional — set `LANGFUSE_TRACING_ENABLED=false` to skip)

## Option A: Docker (recommended)

The fastest way to get running. One command starts the API and PostgreSQL with pgvector.

```bash
git clone <repo-url> my-agent
cd my-agent

# Copy and fill in your env file
cp .env.example .env.development
# Required: JWT_SECRET_KEY and a provider key (OPENAI_API_KEY or DASHSCOPE_API_KEY for Qwen)
# Optional: LANGFUSE_* keys (or set LANGFUSE_TRACING_ENABLED=false)

make install       # installs Python deps + pre-commit hooks
make docker-up     # starts API (port 8000) + PostgreSQL
make docker-migrate # runs Alembic migrations inside the app container
```

Open [http://localhost:8000/docs](http://localhost:8000/docs).

## Option B: Local Python

```bash
git clone <repo-url> my-agent
cd my-agent

cp .env.example .env.development
# Fill in: provider API key, JWT_SECRET_KEY, POSTGRES_* (point to your DB)

make install       # installs deps + pre-commit hooks
make migrate       # creates tables via Alembic
make dev           # starts server with hot reload on port 8000
```

### Windows PowerShell

The Makefile uses GNU Make and Bash, which are not included with PowerShell. From the repository root, use the project virtual environment directly:

```powershell
Copy-Item .env.example .env.development
# Set JWT_SECRET_KEY, a model API key, and POSTGRES_* in .env.development.
# Use POSTGRES_HOST=localhost for a database running on this machine.
uv sync
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open the locally running API at `http://127.0.0.1:8000/docs`. On machines where Docker or WSL also listens on port 8000 over IPv6, `localhost` can reach a different API instance.

For Docker Compose from PowerShell, run `docker compose --env-file .env.development up -d --build db app`, then `docker compose --env-file .env.development exec app /app/.venv/bin/alembic upgrade head`. The container uses `POSTGRES_HOST=db` automatically.

## Your first API call

### 1. Register a user

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "Secret123!", "username": "you"}'  # pragma: allowlist secret
```

Returns a `user_id` and a JWT token.

### 2. Create a session

```bash
curl -X POST http://localhost:8000/api/v1/auth/session \
  -H "Authorization: Bearer <token from step 1>"
```

Returns a `session_id` and a session-scoped JWT.

### 3. Chat

```bash
curl -X POST http://localhost:8000/api/v1/chatbot/chat \
  -H "Authorization: Bearer <session token>" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello!"}]}'
```

Or use the streaming endpoint for real-time responses:

```bash
curl -X POST http://localhost:8000/api/v1/chatbot/chat/stream \
  -H "Authorization: Bearer <session token>" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello!"}]}'
```

## Customising the agent

The parts you'll most likely change:

| What | Where |
|---|---|
| Agent personality & instructions | `app/core/prompts/system.md` |
| Available tools | `app/core/langgraph/tools.py` |
| LLM models & fallback order | `app/services/llm.py` → `LLMRegistry.LLMS` |
| Memory collection name | `LONG_TERM_MEMORY_COLLECTION_NAME` in `.env` |

## Running pre-commit hooks

Hooks run automatically on `git commit`. To run manually:

```bash
make pre-commit
```

Hooks include: trailing whitespace, YAML/TOML/JSON validation, secret detection, ruff lint + format.

## Troubleshooting

**Database connection error on startup**
Make sure PostgreSQL is running and `POSTGRES_*` vars in your `.env` match. With Docker: `make docker-up` handles this (including migrations).

**`could not translate host name "db"`**
`POSTGRES_HOST=db` only resolves *inside* the Docker network (it's the Compose
service name). If you run a command on your host (e.g. `make migrate` or `make dev`
in the local-Python flow), set `POSTGRES_HOST=localhost` instead — the DB's port is
published to the host via `docker-compose.yml`. Inside the container, keep `db`.

**`detect-secrets` blocking a commit**
If it's a false positive, add `# pragma: allowlist secret` to the end of the flagged line.

**Langfuse errors**
Set `LANGFUSE_TRACING_ENABLED=false` in your `.env` to disable tracing entirely during development.
