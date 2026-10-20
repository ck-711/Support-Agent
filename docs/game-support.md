# DOTS Survivors player support

The player page is at `/support`; the staff desk is at `/support/admin`. Both use the existing FastAPI service. The support agent uses a separate LangGraph retrieval → allowlisted tool calls → answer workflow.

## Start locally

1. Install dependencies with `uv sync` (Python 3.13 or newer). On systems with GNU Make and Bash, `make install` is also available.
2. Copy `.env.example` to `.env.development` and set a strong `JWT_SECRET_KEY`, PostgreSQL connection settings, and the key for `DEFAULT_LLM_MODEL`. The support assistant uses the existing LLM registry and fallback service. Set Langfuse credentials and `LANGFUSE_TRACING_ENABLED=true` when tracing is required.
3. Start PostgreSQL with pgvector and apply migrations. The support migrations create conversation, ticket, knowledge, document, and vector tables, and seed five published facts from the [Steam store page](https://store.steampowered.com/app/3326810/DOTS_Survivors/).
4. Start the API and open `http://localhost:8000/support`.

On Windows PowerShell, run these commands from the repository root:

```powershell
uv sync
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

For a locally running database, set `POSTGRES_HOST=localhost` in `.env.development`; `db` resolves only inside the Docker network. Open the local support pages at `http://127.0.0.1:8000/support` and `http://127.0.0.1:8000/support/admin`. If Docker or WSL also listens on port 8000 over IPv6, `localhost` may reach that other API instance. On a shell with GNU Make and Bash, `make migrate` and `make dev` remain available.

The player assistant waits at most `SUPPORT_LLM_TIMEOUT_SECONDS` (20 seconds by default). If the model is unavailable, it shows a relevant published article with its source when one matches the question; a reported malfunction still opens a ticket. Source articles are shown in their stored language in this fallback path.

For Docker, set the same environment variables in `.env.development`. On PowerShell, run `docker compose --env-file .env.development up -d --build db app`, then `docker compose --env-file .env.development exec app /app/.venv/bin/alembic upgrade head`. With GNU Make and Bash, use `make docker-up` and `make docker-migrate`. The project Compose file exposes the API at port 8000. The startup script accepts a DashScope key for a Qwen default model or an OpenAI key for a GPT/OpenAI default model.

The repository's existing generic agent prewarms a Postgres checkpointer and mem0 on startup. Support chat stores its own conversation records and does not depend on generic agent memory.

## Set up staff access

The staff desk uses existing `/api/v1/auth` accounts. Register a user through `/docs` with `POST /api/v1/auth/register`. Passwords must be 8–64 characters and include an uppercase letter, a lowercase letter, a digit, and a supported special character such as `!`, `@`, or `#`. Note the returned numeric user ID, and set `SUPPORT_ADMIN_USER_IDS` to that ID (or a comma-separated list of IDs). Restart the app after changing the environment. Staff then sign in on `/support/admin`. An empty allowlist denies all staff operations. Use user IDs instead of email addresses so a public registration cannot claim a privileged email before the owner does.

The staff desk can view ticket counts and full transcripts, review and reply to player reports, create or revise knowledge drafts, and upload, preview, replace, reindex, publish, or delete documents. Editing a published entry returns it to draft. Staff replies appear in the player's original conversation; no email is sent. Players register or sign in at `/support` and can resume their current conversation on another device. An old anonymous conversation saved in this browser is imported once after sign-in; its old bearer token is then invalidated.

The staff desk language selector offers Chinese, English, and Spanish. Both pages use the same dark support interface.

## Player API

All routes below are under `/api/v1/support` and are rate limited. Register or sign in through `/api/v1/auth`, then send `Authorization: Bearer <account JWT>` for player routes. Each active conversation belongs to one user ID. Legacy `X-Support-Token` headers alone cannot access player data.

| Route | Purpose |
| --- | --- |
| `POST /conversations` | Resume or start the signed-in player's current session; choose `en`, `es`, or `zh`. |
| `POST /conversations/claim` | Import one old anonymous session by its token after sign-in. |
| `GET /messages`, `POST /messages` | Read or send chat messages; use `before=<message UUID>` for older pages and `after=<message UUID>` to catch up after disconnection. Pages contain up to 100 messages. |
| `GET /tickets`, `POST /tickets` | Check or submit issue reports. |
| `GET /knowledge` | Read only published sourced articles. |
| `GET /documents/{id}` | Read a published document cited by the agent. |
| `GET /images/{id}` | Read an image belonging to a published document. |
| `POST /tools/{name}` | Call an allowlisted, player-scoped business tool. |
| `DELETE /conversations/current` | Immediately erase the session, messages, and tickets. |
| `/admin/*` | Staff queue, full ticket context, knowledge and document management; requires an allowlisted account JWT. |

The agent can call `search_knowledge`, `list_my_tickets`, and `request_human_handoff`. The same names are available through `POST /tools/{name}` with `{"arguments": {...}}`; the server injects the player's conversation scope. Unresolved, complex, and high-risk questions create or reuse a human ticket with a full transcript snapshot. Staff read the current transcript through `GET /admin/tickets/{id}/context` and reply in the original conversation. The assistant cannot read or modify Steam accounts, save files, payment details, or game telemetry.

## Knowledge and privacy

The initial entries contain only facts visible on the linked Steam listing: game overview, listed weapons, Steam features, minimum requirements, and listed languages. Release notes, issue resolutions, upgrade rules, and other game-specific claims must be added as sourced drafts and reviewed before publication. Keep source URLs current when the game changes.

Staff uploads accept UTF-8 TXT, Markdown, PDF, DOCX, PNG, JPG, and WEBP up to 50 MiB. A PDF may have up to 200 pages; extracted text is limited to 600,000 characters and 800 retrieval passages. Local Tesseract OCR reads images embedded in PDF and DOCX files, and directly uploaded images. OCR text enters the preview and retrieval passages; staff should review it before publishing. Parsed text is split into overlapping passages and embedded in 1024 dimensions. Published staff articles, document chunks, and reviewed image captions share one Milvus collection. PostgreSQL remains the source of truth for text and review state; every Milvus hit is checked against the current publication status and revision. The Docker image installs Chinese, English, and Spanish Tesseract language packs. The default embedding model is DashScope `text-embedding-v3` when `DASHSCOPE_API_KEY` is set, otherwise OpenAI `text-embedding-3-small`. Configure `SUPPORT_EMBEDDING_MODEL`, `SUPPORT_EMBEDDING_API_KEY`, and `SUPPORT_EMBEDDING_BASE_URL` for another compatible 1024-dimensional service, including BGE.

Run `docker compose --env-file .env.development up -d --build app` to start Milvus and its dependencies with the API. For an existing deployment, run `docker compose --env-file .env.development exec app /app/.venv/bin/python -m scripts.rebuild_support_milvus` once to backfill all published content. Repeat the backfill after changing the embedding model. The Milvus WebUI is available at `http://127.0.0.1:19091/webui/`. `SUPPORT_MILVUS_URI` defaults to the local port for a host process and is set to the Compose service address inside the app container.

Support conversations and tickets expire 30 days after creation. The application runs a daily purge and erases a session immediately on request. Do not ask players for passwords, payment details, or personal contact information. If Langfuse tracing or a third-party LLM is enabled, configure its retention separately to match your privacy policy; those external copies are outside the support database purge.

Staff may attach up to 20 PNG, JPEG, or WebP images per document, each up to 10 MiB. Images are normalized and stored in a private MinIO bucket. Staff review a description for each image before publishing the parent document; descriptions receive 1024-dimensional embeddings and are searchable. The optional vision button drafts a caption for staff review. With `DASHSCOPE_API_KEY`, it defaults to `qwen3-vl-plus`; configure `SUPPORT_VISION_MODEL`, `SUPPORT_VISION_API_KEY`, and `SUPPORT_VISION_BASE_URL` for another compatible model. The image is sent to that model only when staff request a draft. Cited images appear in chat, and the public image route checks document publication. MinIO runs on localhost ports 19000 (API) and 19001 (console), with data in the `minio-data` volume.

## Verification

Run `uv run ruff check .`, `uv run pyright`, and `uv run pytest tests/test_support.py`. With a migrated local PostgreSQL, set `SUPPORT_INTEGRATION=1` and run `uv run pytest tests/test_support_integration.py tests/test_support_rag_integration.py tests/test_support_account_integration.py`. On Windows, the async psycopg integration tests use a selector event loop. The tests mock model and embedding calls to avoid external requests.
## v1.1–v1.3 operations

Staff can claim and release active tickets. While a ticket is open or investigating, player messages remain in the conversation and AI replies pause. Staff replies appear in the same thread; resolving or closing the ticket resumes AI on the next player message. Both pages use authenticated server-sent events with a two-second database poll, with manual refresh available on disconnection.

Players can rate each AI response. The staff desk shows rating totals, unassigned reports, and sources due for review. Published knowledge and documents receive a 90-day review date. The `evals/support_cases.jsonl` dataset contains 18 synthetic English, Spanish, and Chinese regression cases. Run `uv run python -m evals.support_eval --output support-eval.json --baseline evals/baselines/v1.3.1.json` to compare citation title coverage, handoff errors, and latency. These cases are synthetic and do not claim to measure actual player traffic.

The support agent and `search_knowledge` tool search the shared Milvus collection first. Existing PostgreSQL text and vector queries remain an availability fallback while older sources are backfilled. PDF chunks retain their source page; citation links expose the extracted page text. Apply the latest schema with `uv run alembic upgrade head` before serving the new API.
