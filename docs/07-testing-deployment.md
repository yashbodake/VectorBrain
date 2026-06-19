# 07 — Testing & Deployment

## Local Development Setup

### 1. Database

```bash
# via Docker (simplest path — pulls an image with pgvector pre-installed)
docker run -d \
  --name vectorbrain-db \
  -e POSTGRES_USER=vectorbrain \
  -e POSTGRES_PASSWORD=vectorbrain \
  -e POSTGRES_DB=vectorbrain \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

Then enable the extension and run migrations:
```bash
psql postgresql://vectorbrain:vectorbrain@localhost:5432/vectorbrain -c "CREATE EXTENSION IF NOT EXISTS vector;"
alembic upgrade head
```

### 2. Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GROQ_API_KEY and DATABASE_URL
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
cp .env.example .env   # confirm VITE_API_BASE_URL points at the backend
npm run dev
```

## Backend Unit Tests

Use `pytest`. Minimum coverage for Lite scope:

- **Chunking**: given a known parsed document, chunks come back non-empty, within the configured token size, with correct `page_number`/`chunk_index` ordering
- **Embedding dimension**: embedding a sample string returns a 384-length vector
- **Retrieval ranking**: seed the test DB with a few known chunks + embeddings, confirm a query returns them ordered by distance as expected (use simple, clearly-separable test vectors rather than real embeddings for this one, to keep the test deterministic and fast)
- **Document status transitions**: a mocked ingestion run moves a document from `uploaded` → `processing` → `ready`; a forced exception moves it to `failed` with `error_message` set

## API Integration Tests

Use `httpx.AsyncClient` against the FastAPI app (or `TestClient`).

- `POST /api/documents` — happy path returns `201` with expected shape; non-PDF upload returns `400`; oversized file returns `413`
- `GET /api/documents` — returns uploaded documents
- `DELETE /api/documents/{id}` — removes the document; second delete of the same ID returns `404`
- `POST /api/chat` — with no ready documents, returns a graceful "no documents ready" response rather than erroring; with seeded chunks, returns a streamed response containing at least one citation

For tests that would otherwise hit the real Groq API, mock the Groq client — don't burn real API calls (or risk flaky tests from network issues) in the test suite.

## Manual QA Checklist (run before considering Phase 5 done)

- [ ] Upload 3 PDFs of varying size, confirm all reach `ready` status without manual intervention
- [ ] Upload a non-PDF file, confirm a clear error, no crash
- [ ] Upload a corrupted/unparseable PDF, confirm it lands in `failed` with a readable error message, not a stuck `processing` state
- [ ] Ask a question whose answer spans two different documents, confirm citations from both appear
- [ ] Ask a question with no relevant content in any document, confirm the app says so instead of inventing an answer
- [ ] Delete a document, confirm its chunks are gone and it no longer contributes to chat answers
- [ ] Refresh the browser mid-chat, confirm the app doesn't crash (Lite scope has no persisted chat history — losing the thread on refresh is expected and fine, just shouldn't error)
- [ ] Kill and restart the backend/DB, confirm previously uploaded documents and their chunks are still there (i.e., nothing was only in memory)

## Environment Variables

See `.env.example` at the project root for the full list. Required at minimum:

- `GROQ_API_KEY` — from the Groq console
- `DATABASE_URL` — Postgres connection string
- `MAX_UPLOAD_SIZE_MB` — upload size cap enforced by `POST /api/documents`

## Deployment (Lite scope)

A single small VM or a couple of managed services is enough — this is not a project that needs orchestration:

**Option A — single VM / Docker Compose**
- One `docker-compose.yml` with services: `db` (pgvector image), `backend` (FastAPI), and the frontend built as static files served by the backend (`StaticFiles` mount) or nginx.
- Simplest to reason about, simplest to back up (one disk, one Postgres data volume).

**Option B — split managed services**
- Backend + Postgres on Railway or Render (both offer managed Postgres with extensions, confirm `pgvector` is available on whatever tier is used)
- Frontend (`vite build` output) on Vercel or Netlify, pointed at the deployed backend's URL via `VITE_API_BASE_URL`

Either is fine for a personal project; pick based on what's already familiar rather than over-thinking it. The one hard requirement: wherever Postgres lands, confirm `pgvector` is actually installable/enabled on that host before committing to it — not all managed Postgres tiers include it by default.

## Things Specifically NOT Worth Doing for Lite Scope

(Listed explicitly so an agent doesn't gold-plate this beyond what was asked for.)

- CI/CD pipelines
- Horizontal scaling / load balancing
- Rate limiting beyond basic upload size caps
- Structured logging/observability stack (basic `print`/standard logging is enough)
- Automated frontend E2E tests (manual QA checklist above is sufficient for this scope)
