# VectorBrain

A "second brain" study tool: upload multiple PDFs, ask questions, get answers grounded in the documents with page-level citations. Multi-document RAG over **PostgreSQL + pgvector**, **FastAPI**, **Docling**, **bge-small-en-v1.5**, and **Cerebras**.

> **Status:** ✅ Lite build complete (Phases 1–5). See `PROGRESS.md`.

## Architecture

```
Vue 3 SPA  <-->  FastAPI (async)  <-->  PostgreSQL + pgvector
                      |   |
                 Docling   LLM (Cerebras, OpenAI-compatible)
                      |
               bge-small-en-v1.5 (local, 384-dim)
```

See `docs/02-architecture.md` for the full diagram and rationale. The frontend talks to the backend over REST (`/api/documents`) and SSE-over-POST (`/api/chat`).

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Frontend | Vue 3 (`<script setup>`) + Pinia + Vite | Two-panel layout, streaming chat, citation chips |
| Backend | FastAPI (Python 3.11+), async end-to-end | Pydantic at every boundary, business logic in `services/` |
| Database | PostgreSQL 16 + pgvector 0.8 | `vector(384)` + HNSW index, FK cascade |
| Parsing | Docling (`HybridChunker`) | Structure-aware, preserves page boundaries for citations |
| Embeddings | `BAAI/bge-small-en-v1.5` (384-dim) | Local inference via `sentence-transformers`, normalized |
| LLM | Cerebras `gpt-oss-120b` | OpenAI-compatible API (originally specced as Groq — see PROGRESS.md) |

## Quick start

### 1. Database (Docker)

```bash
docker compose up -d            # pgvector/pgvector:pg16 on host port 5433
docker compose ps               # wait until STATUS is "healthy"
```

Port **5433** is used deliberately so it doesn't clash with a local Postgres on 5432 (the connection string in `.env` reflects this). See `PROGRESS.md` Spec Deviations.

### 2. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Torch + torchvision must be a matched CPU pair from the PyTorch index
# (otherwise: "operator torchvision::nms does not exist"):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Apply the schema (creates extension vector, documents, chunks, HNSW index):
alembic upgrade head

uvicorn app.main:app --reload --port 8000
```

Open the interactive API docs at <http://localhost:8000/docs>.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173 (proxies /api -> :8000)
```

### 4. Environment

Copy `.env.example` → `.env` at the repo root and fill in:

```ini
DATABASE_URL=postgresql+asyncpg://vectorbrain:vectorbrain@localhost:5433/vectorbrain
CEREBRAS_API_KEY=...                      # from https://cerebras.ai
CEREBRAS_BASE_URL=https://api.cerebras.ai/v1
CEREBRAS_MODEL=gpt-oss-120b
```

The embedding model (`bge-small-en-v1.5`) and Docling's layout/OCR models download on first use (~hundreds of MB), then cache under `~/.cache/huggingface`. Set `HF_HUB_OFFLINE=1` to run fully offline once cached.

### 5. Smoke test

```bash
# upload a PDF, watch status flip uploaded -> processing -> ready
curl -F file=@some.pdf http://localhost:8000/api/documents
curl http://localhost:8000/api/documents

# ask a question — answer streams back with page-level citations
curl -N -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is this document about?"}'
```

## Running the tests

```bash
cd backend && source .venv/bin/activate
HF_HUB_OFFLINE=1 python -m pytest      # 24 tests, ~2.5s
```

Tests use the real Postgres+pgvector (so SQL and the `<=>` operator are genuinely exercised) but **mock the embedding model and the LLM** for speed and determinism — no torch, no network, no API spend. The suite uses a session-scoped event loop because asyncpg binds connections to the loop that created them (see `backend/pyproject.toml` `[tool.pytest.ini_options]`).

## Project layout

```
.
├── AGENT.md, PROGRESS.md          # agent entry points + live build tracker
├── docs/                          # product/architecture/API specs (source of truth)
├── docker-compose.yml             # local pgvector DB
├── .env / .env.example            # runtime config (gitignored / template)
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI app + CORS + router wiring
│   │   ├── api/                   # route handlers: documents.py, chat.py
│   │   ├── services/              # business logic: ingestion, retrieval, chat,
│   │   │                          #   embeddings, parsing, chunking, generation
│   │   ├── models/                # Pydantic schemas (documents, chat)
│   │   ├── db/                    # SQLAlchemy models + session
│   │   └── core/                  # config (pydantic-settings)
│   ├── alembic/                   # migrations (initial schema + pgvector)
│   └── tests/                     # conftest + unit + integration tests
└── frontend/
    ├── src/
    │   ├── api/client.js          # axios REST + fetch SSE-over-POST
    │   ├── stores/                # Pinia: documents.js, chat.js
    │   ├── components/            # DocumentManager, ChatInterface, cards, chips
    │   └── App.vue, main.js
    └── vite.config.js             # dev proxy /api -> :8000
```

## Data flow

**Upload → ingestion (background):** `POST /api/documents` stores the raw PDF, inserts a `documents` row (`status='uploaded'`), returns 201 immediately, then a `BackgroundTasks` job runs Docling parse → `HybridChunker` → bge embeddings → writes `chunks` with page numbers, flipping status `uploaded → processing → ready` (or `failed` with a readable `error_message`).

**Chat (RAG):** `POST /api/chat` embeds the question (BGE query prefix) → pgvector `<=>` top-6 across `ready` docs filtered by a 0.5 distance threshold → builds a numbered-excerpt prompt → streams the LLM answer token-by-token via SSE → emits a `done` event with de-duplicated `(filename, page)` citations from the retrieved chunks. If nothing clears the threshold it declines without calling the LLM (no hallucination).

## Deployment (Lite scope)

Not deployed to a live host (out of Lite scope). Two options documented in `docs/07-testing-deployment.md`:

- **Single Docker Compose stack** — `db` (pgvector) + `backend` (FastAPI) + frontend built as static files served by the backend or nginx. Simplest, one volume to back up.
- **Split managed services** — backend + Postgres on Railway/Render (confirm pgvector is available on the chosen tier), frontend on Vercel/Netlify pointed at the backend via `VITE_API_BASE_URL`.

The hard requirement wherever Postgres lands: confirm `pgvector` is installable/enabled there before committing.

## Notes & deviations

See `PROGRESS.md` → "Spec Deviations" for the full list. The notable ones:

- **LLM: Cerebras instead of Groq** (user-provided key; OpenAI-compatible API).
- **DB on port 5433** (local Postgres 12 occupies 5432 and is too old for pgvector).
- **`transformers` pinned `<5`** (docling 2.x imports symbols removed in v5).
- **Ingestion runs serialized** via FastAPI `BackgroundTasks` — the documented v1 scope; a task queue is the named future upgrade for concurrent large uploads.
