# 02 — Architecture

## System Overview

```
┌─────────────────┐         ┌──────────────────────┐         ┌─────────────────────┐
│   Vue.js 3 SPA   │ <-----> │   FastAPI Backend     │ <-----> │ PostgreSQL+pgvector  │
│  (Vite dev/build)│  REST + │  (async, Python 3.11) │  asyncpg│  documents + chunks  │
│                  │   SSE   │                       │         │                      │
└─────────────────┘         └──────────┬────────────┘         └─────────────────────┘
                                        │
                              ┌─────────┴─────────┐
                              │                    │
                       ┌──────▼──────┐     ┌───────▼───────┐
                       │   Docling   │     │   Groq API     │
                       │ (PDF parse) │     │ (LLM inference)│
                       └─────────────┘     └────────────────┘
                              │
                       ┌──────▼──────┐
                       │bge-small-en │
                       │ -v1.5 (local│
                       │ embeddings) │
                       └─────────────┘
```

## Components & Responsibilities

### Frontend (Vue.js 3)
- Renders document list + chat UI
- Uploads files via multipart form POST
- Polls or receives status updates for documents still processing
- Opens a streaming connection (SSE or fetch + ReadableStream) for chat answers
- Holds UI state in Pinia: `documents` store, `chat` store

### Backend (FastAPI)
- Exposes REST endpoints for document CRUD and the chat endpoint (see `docs/04-backend-api-spec.md`)
- Orchestrates the ingestion pipeline (Docling → chunk → embed → store) as a background task per upload
- Orchestrates the query pipeline (embed query → pgvector search → build prompt → call Groq → stream tokens → attach citations)
- Owns all calls to Docling, the embedding model, and Groq — frontend never talks to these directly

### Database (PostgreSQL + pgvector)
- Single source of truth for document metadata and chunk embeddings
- `pgvector` extension handles similarity search via the `<=>` (cosine distance) operator directly in SQL — no separate vector database process to run
- See `docs/03-database-schema.md` for full schema

### Docling
- Parses uploaded PDFs into structured text while preserving page boundaries (and headings/tables where present)
- Runs synchronously inside the background ingestion task, not in the request/response cycle of the upload endpoint

### Embedding model (`bge-small-en-v1.5`)
- Runs locally via `sentence-transformers`, no external API call, no per-token cost
- Used identically at ingestion time (embedding chunks) and query time (embedding the user's question) — this symmetry is required for cosine similarity to be meaningful
- Output dimension: 384 (fixed — matches the `vector(384)` column in the schema)

### Groq API
- Used only for the final answer generation step, given a prompt that already contains the retrieved chunks
- Called in streaming mode so the frontend can render tokens as they arrive

## Data Flow: Document Upload

1. Frontend POSTs file to `POST /api/documents`
2. Backend saves the raw file, inserts a `documents` row with `status = 'uploaded'`, returns immediately with the document ID
3. Backend kicks off a background task:
   a. Docling parses the PDF → list of (text, page_number) segments
   b. Chunker (Docling `HybridChunker`) splits into chunks with overlap (see `docs/05-rag-pipeline-spec.md`)
   c. Each chunk is embedded (384-dim vector)
   d. Chunks + embeddings + page numbers are inserted into the `chunks` table
   e. `documents.status` updated to `'ready'` (or `'failed'` with an error message on exception)
4. Frontend polls `GET /api/documents/{id}` (or `GET /api/documents`) to reflect status changes in the UI

## Data Flow: Chat Query

1. User submits a question via the chat input
2. Frontend POSTs to `POST /api/chat` with `{ question }`
3. Backend embeds the question with the same embedding model
4. Backend runs a pgvector similarity search across `chunks` for all `ready` documents, takes top-k (see `docs/05-rag-pipeline-spec.md` for k value and any per-document caps)
5. Backend assembles a prompt: system instructions + retrieved chunks (each tagged with filename + page) + the user's question
6. Backend calls Groq in streaming mode, forwards tokens to the frontend as they arrive (SSE)
7. Once generation completes, backend sends a final event listing the distinct (filename, page) citations actually used
8. Frontend renders the streamed answer, then appends the citation line(s) below it

## Why These Choices

- **Vue.js 3**: reactive, component-based, well suited to managing multiple documents and streaming chat state without a heavy framework.
- **FastAPI**: native async support pairs well with streaming LLM responses and background ingestion tasks; Pydantic gives free request/response validation.
- **PostgreSQL + pgvector**: avoids standing up a separate vector database (Pinecone, Qdrant, etc.) for a personal-scale project — one database, one connection pool, one backup story.
- **Docling**: structure-aware parsing keeps page numbers and document structure intact, which citations depend on. Plain text extraction (e.g. raw PyPDF text dump) loses page boundaries more easily.
- **bge-small-en-v1.5**: 384-dim is small enough to be fast in pgvector with a basic index, while still being a competent general-purpose embedding model; running it locally avoids per-call embedding API costs and rate limits during bulk ingestion.
- **Groq**: chosen specifically for inference speed, which directly serves the product's "fast, second-brain" feel — this is a latency-sensitive choice, not just a cost one.

## Deployment Topology (Lite)

For a personal project, the simplest viable setup is:

- Single VM or small managed instances: FastAPI backend + Postgres (with pgvector) co-located, e.g. on Railway, Render, or a single Docker Compose stack
- Frontend built as static assets (`vite build`) and served either by the same backend (via FastAPI `StaticFiles`) or a static host (Vercel/Netlify) pointed at the backend's API URL

Full instructions: `docs/07-testing-deployment.md`.
