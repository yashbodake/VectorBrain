# Coding Conventions

Applies to all code written for this project, by any agent, in any session. Read this before writing code, not after.

## Backend (Python / FastAPI)

- Python 3.11+, type hints on every function signature (params and return type)
- Pydantic models for every request/response body — no raw dicts passed across layer boundaries
- Async all the way down: route handlers `async def`, DB access via `asyncpg`/`SQLAlchemy async`, no blocking calls (including the embedding model's `.encode()` and Docling parsing) inside the event loop — run those in a thread pool executor or as part of a `BackgroundTasks` job, not inline in an `async def` route handler
- Errors: raise `HTTPException` with the right status code and a `detail` message; don't return `200` with an error payload baked into the body
- Project layout:
  ```
  backend/
    app/
      main.py              # FastAPI app, router registration, CORS
      api/                 # route handlers, one file per resource (documents.py, chat.py)
      services/            # ingestion.py, retrieval.py, embeddings.py — business logic, no FastAPI imports here
      models/              # Pydantic schemas
      db/                  # SQLAlchemy models, session management
      core/                # config.py (settings), constants
    alembic/
    tests/
  ```
- Naming: `snake_case` for functions/variables, `PascalCase` for Pydantic/SQLAlchemy classes
- No business logic inside route handler functions beyond calling into `services/` — handlers parse the request, call a service function, shape the response. Keeps `services/` testable without spinning up FastAPI.
- Config via `pydantic-settings`, reading from `.env` — never hardcode API keys, connection strings, or model names that might change (the Groq model string especially — see `docs/05-rag-pipeline-spec.md`)

## Frontend (Vue.js 3)

- `<script setup>` Composition API only — no Options API, no mixing the two styles in the same file
- Components: `PascalCase` filenames matching the component name (`DocumentCard.vue`)
- Composables (if used beyond Pinia stores): `useXyz.js` naming, in `src/composables/`
- Pinia stores in `src/stores/`, one file per store, action methods do the API calls — components never call `fetch`/`axios` directly (see `docs/06-frontend-spec.md`)
- Props: always typed (`defineProps<{ ... }>()` or the runtime equivalent with explicit types) — no untyped prop bags
- No business logic in templates beyond simple conditionals/loops — anything more complex goes in a computed property or the store

## Git / Commits

- Conventional Commits style: `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`
- One logical change per commit where reasonable — don't bundle "add upload endpoint" with "fix typo in README" in the same commit
- Commit messages describe *why* when it's not obvious from the diff alone, not just *what*

## Testing

- Backend: `pytest`, test files mirror the module they test (`services/ingestion.py` → `tests/test_ingestion.py`)
- Test function names describe the scenario: `test_upload_rejects_non_pdf_file`, not `test_upload_2`
- Mock external calls (Groq API, anything hitting the network) in tests — never depend on live external services for the test suite to pass

## General

- Don't introduce a new dependency without a real reason tied to the spec — if `docs/05-rag-pipeline-spec.md` says Docling + sentence-transformers + Groq, that's the stack; don't swap in LangChain/LlamaIndex as an abstraction layer on top "for convenience" unless the user asks for it. Direct calls to these libraries are simpler to reason about and debug at this project's scale.
- If something in a spec doc seems wrong while implementing it, fix the doc and note it in `PROGRESS.md` under "Spec Deviations" — don't quietly diverge from the written spec without a trail (see `AGENT.md`).
