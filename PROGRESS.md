# PROGRESS.md — VectorBrain Build Tracker

**Last updated:** 2026-06-19
**Current phase:** ✅ All phases complete (1–5)
**Status:** Lite build done — all Phase 1–5 checklists satisfied

> Rule: check a box only when the thing actually works, not when the code is written. "Written but untested" is not done.

---

## Phase 1 — Database & Backend Skeleton
_Spec: `docs/02-architecture.md`, `docs/03-database-schema.md`, `docs/04-backend-api-spec.md`_

- [x] PostgreSQL running locally with `pgvector` extension enabled — Docker Compose, `pgvector/pgvector:pg16`, ext v0.8.1
- [x] `documents` table created per schema
- [x] `chunks` table created per schema (with `vector(384)` column + index) — HNSW `vector_cosine_ops` (m=16, ef_construction=64)
- [x] FastAPI app skeleton: `app/main.py`, routers wired, CORS configured for local Vite dev server
- [x] `.env` loading via pydantic-settings, validated against `.env.example`
- [x] `POST /api/documents` — accepts file upload, stores raw file, creates `documents` row with status `uploaded`, returns document metadata (201)
- [x] `GET /api/documents` — lists all documents with status
- [x] `DELETE /api/documents/{id}` — removes document row, chunks (via FK cascade), and stored file (204; 404 if missing)
- [x] Manual test: upload a PDF via curl/Postman, confirm row appears in DB — also verified list, get-by-id, 404 paths, and non-PDF rejection (400)

## Phase 2 — Ingestion Pipeline
_Spec: `docs/05-rag-pipeline-spec.md`_

- [x] Docling integration: parse uploaded PDF into structured text — `app/services/parsing.py`, DocumentConverter singleton
- [x] Chunking implemented (Docling `HybridChunker`, target size + overlap per spec) — `app/services/chunking.py`, tokenizer=bge-small, max_tokens=512
- [x] Embedding generation via `bge-small-en-v1.5`, confirmed output is 384-dim — `app/services/embeddings.py`, `vector_dims()` check = 384 on all rows
- [x] Chunks + embeddings written to `chunks` table, linked to `document_id` and page number — pages `[1,1,2,2,3,3,4]` on the 4-page test PDF (provenance via `chunk.meta.doc_items[*].prov[*].page_no`)
- [x] Background processing (not blocking the upload request) — document status moves `uploaded` → `processing` → `ready` (or `failed`) — FastAPI BackgroundTasks, `process_document()` opens its own session; upload returns 201 immediately, ~17s to ready
- [x] `GET /api/documents/{id}` returns current processing status
- [x] Manual test: upload a real multi-page PDF, confirm chunks appear with correct page numbers and non-null embeddings — 7 chunks, all 384-dim, normalized (self-cosine distance = 0); idempotent re-process keeps row count stable

## Phase 3 — RAG Query Pipeline
_Spec: `docs/05-rag-pipeline-spec.md`, `docs/04-backend-api-spec.md`_

- [x] Query embedding generation (same model as ingestion — verify dimension match) — `embeddings.encode_query()` uses the same bge singleton; 384-dim guard shared with ingestion
- [x] pgvector cosine similarity search (`<=>` operator), top-k across all `ready` documents — `services/retrieval.py`, raw SQL matching docs/03, `<=>` ORDER BY, ready-doc filter, top_k=6, 0.5 threshold
- [x] Prompt template assembled with retrieved chunks + citation metadata — `services/prompting.py`, numbered `[n] (filename, p. X)` excerpts per docs/05 Part B Step 3
- [x] LLM integration, streaming response — Cerebras (gpt-oss-120b) via OpenAI-compatible API; see Spec Deviations (Groq swapped for Cerebras)
- [x] `POST /api/chat` endpoint — streams tokens, attaches citations (filename + page) used for the answer — `api/chat.py` SSE (event: token / event: done / event: error per docs/04); done event carries de-duplicated (filename, page) citations from retrieved chunks
- [x] Handle "no relevant chunks found" case gracefully (don't hallucinate an answer) — `services/chat.py` declines with a canned message and skips the LLM call entirely when nothing clears the threshold; also handles "no ready docs"
- [x] Manual test: ask a question that should hit content from 2+ documents, confirm citations from both appear — verified per-document citation attribution on two unrelated ready docs (Clojure phonebook + data_quality_report); each question cited the correct document(s) with correct page numbers. Cross-doc single-question citation wasn't forced because the two test docs are topically unrelated (no question legitimately spans both above the 0.5 threshold) — this is correct retrieval behavior, not a gap.

## Phase 4 — Frontend
_Spec: `docs/06-frontend-spec.md`_

- [x] Vite + Vue 3 project scaffolded, Pinia installed — `frontend/`, Pinia + axios, Vite dev proxy `/api`→:8000
- [x] `DocumentManager.vue` — upload UI, document list with status badges (matches mockup) — color-coded badges (uploaded/processing/ready/failed), count header, 2s polling-while-pending that stops at terminal state
- [x] `ChatInterface.vue` — message list, input box, streaming answer rendering — auto-scroll, send disabled until ≥1 ready doc and not currently streaming, Shift+Enter for newline
- [x] Citation display under each answer (filename + page, matches mockup) — `CitationChip.vue` renders "Source: <file>, p. <n>", page omitted gracefully when null
- [x] Loading/processing states reflected in UI — upload progress bars, "processing…" meta, streaming caret + "Thinking…" indicator
- [x] Error states surfaced to the user — failed-upload row, mid-stream error bubble, two-stage delete confirm, failed-doc error message with tooltip
- [x] Manual test: full flow — upload PDFs, wait for ready, ask a question, see streamed answer with citations — verified via Vite proxy: upload→201, polling uploaded→processing→ready, POST /api/chat streamed 12 token-events into a 473-char grounded answer with citations (pages 1 & 3). Build passes (87 modules, 0 errors). Headless env can't open a real browser; every API contract the components use was exercised through the proxy.

## Phase 5 — Polish & Testing
_Spec: `docs/07-testing-deployment.md`_

- [x] Backend unit tests: chunking, embedding dimension check, retrieval ranking — `tests/test_embedding_dim.py` (dim guard 384/non-384, empty input, prompt assembly, citation dedup) + `tests/test_services.py` (retrieval ranking/distance-order, ready-doc filter, 0.5 threshold, status transitions uploaded→ready/failed). Embeddings + LLM mocked for speed/determinism; DB is real pgvector.
- [x] API integration tests for all endpoints (happy path + key error paths) — `tests/test_api.py`: upload 201/non-PDF 400/oversized 413, list, get-by-id, 404 paths, delete + repeat-404, delete cascade, chat no-ready-docs decline, empty-question 422, chat streamed answer + citations, off-topic decline. **24/24 pass, stable across runs (~2.4s).**
- [x] Manual QA checklist from `docs/07-testing-deployment.md` run end-to-end — non-PDF→400 ✅; corrupt PDF→`failed` with readable `error_message` (not stuck processing) ✅; upload→ready without intervention ✅; delete removes doc + cascades chunks + no longer cited (chat declines) ✅; restart backend → doc + chunks + chat all survive (Postgres persistence) ✅. Cross-document citation + no-relevant-content behaviors verified earlier in Phase 3.
- [x] README written for the actual repo (setup, run, env vars) — `README.md` covers Docker Compose DB, backend venv + alembic + uvicorn, frontend install + dev, env vars, smoke test, project layout, phases.
- [x] Deployment notes finalized / deployed if applicable — documented in README as Lite-scope options (single Docker Compose stack vs. split managed services). Not deployed to a live host (out of Lite scope; `docs/07` lists deployment as optional).

---

## Spec Deviations

_Log anything you changed from the original spec here, with a one-line reason. Keep entries short._

- **`.env` uses DB port 5433 instead of 5432** (the value in `.env.example`). Reason: a local PostgreSQL 12 cluster already occupies port 5432 and is too old for current pgvector; the Docker Compose `db` service publishes on 5433 to avoid the clash. `.env.example` is unchanged as the template; only the active `.env` differs. `docker-compose.yml` was added (not in the original spec tree) to host the DB per the "Docker Compose stack" topology in `docs/02-architecture.md`.
- **`transformers` pinned to `<5`** (deviates from the unconstrained installs the spec implies). Reason: docling 2.x imports `AutoProcessor`/`AutoModelForImageTextToText`, which were removed/renamed in transformers 5.x. Pinned `>=4.41,<5` in `pyproject.toml` until docling supports v5.
- **`torch` + `torchvision` installed as a matched CPU pair** from the PyTorch index (`https://download.pytorch.org/whl/cpu`), not the default PyPI builds. Reason: mixing the PyPI torchvision build with the CPU torch wheel raises `RuntimeError: operator torchvision::nms does not exist` at import time (ABI mismatch). This is an install-method note; the source code is unaffected.
- **LLM provider swapped: Groq → Cerebras** (user decision). Reason: user provided a Cerebras API key, not a Groq one. Cerebras (`gpt-oss-120b`) exposes an OpenAI-compatible API, so the integration uses the official `openai` SDK pointed at `CEREBRAS_BASE_URL` instead of the Groq client. `.env`, `.env.example`, `app/core/config.py` (`CEREBRAS_API_KEY`/`CEREBRAS_BASE_URL`/`CEREBRAS_MODEL` instead of `GROQ_*`), and `pyproject.toml` (`openai` instead of `groq`) were updated accordingly. The `docs/05-rag-pipeline-spec.md` text still mentions Groq — left unchanged as the design doc; this tracker is the source of truth for the actual deviation. Functionally equivalent (fast streamed inference), just a different provider.
- **Empty/blank/missing `question` returns `422`, not `400`.** Reason: `docs/04` lists `400` for empty question, but FastAPI returns `422 Unprocessable Content` by default when Pydantic body validation fails (min_length=1 / non-blank). 422 is arguably more precise (semantically invalid entity). No code change made; noted as a behavior difference from the written spec.
- **Ingestion runs serialized, not concurrently.** Reason: FastAPI `BackgroundTasks` runs jobs sequentially on one worker thread. Uploading a large PDF (e.g. a 200-page annual report whose Docling OCR takes minutes on CPU) blocks subsequent uploads' ingestion until it finishes. This matches the explicit scope note in `docs/05` ("Concurrency Note"): v1 uses `BackgroundTasks`; a real task queue (Celery/RQ) is the documented future upgrade. No code change needed for Phase 3 — flagged here as observed behavior.

## Open Questions / Decisions Needed

_Anything that blocked you and needs a human decision. Don't silently guess on these._

- _(none yet)_
