# AGENT.md — VectorBrain (Notebook LLM Lite)

> **Read this file first.** This is the entry point for any AI coding agent (Claude Code, Cursor, OpenCode, Windsurf, etc.) picking up work on this project — whether starting fresh or resuming after a context reset or model switch.

## What This Project Is

VectorBrain is a "second brain" study tool: upload multiple PDFs, ask questions, get answers grounded in the documents with page-level citations. It's a multi-document RAG (Retrieval-Augmented Generation) system built as a focused, ~3-day "Lite" implementation.

Full product spec: `docs/01-product-overview.md`

## How To Resume Work (Every Session — Do This First)

1. Read `PROGRESS.md`. It tells you exactly which phase and task is active, and what's already done. **Do not trust your own memory of project state — trust PROGRESS.md.**
2. Read the doc for the **current phase only** (linked from PROGRESS.md). Don't re-read finished phases unless you need a refresher.
3. Check `.claude/rules/coding-conventions.md` before writing any code.
4. After finishing a task, **update PROGRESS.md immediately** — check the box, add a one-line note if anything deviated from spec. Do this before ending your turn, not "later."
5. If you discover the spec is wrong, ambiguous, or incomplete, fix the spec doc itself, log the change under "Spec Deviations" in PROGRESS.md, and proceed. Don't silently improvise without leaving a trail.

## Project Structure (Specs)

| File | Purpose |
|---|---|
| `docs/01-product-overview.md` | What we're building, features, definition of done |
| `docs/02-architecture.md` | System architecture, tech stack, data flow |
| `docs/03-database-schema.md` | PostgreSQL + pgvector schema, migrations |
| `docs/04-backend-api-spec.md` | FastAPI endpoints, request/response contracts |
| `docs/05-rag-pipeline-spec.md` | Docling parsing, chunking, embeddings, retrieval, prompting |
| `docs/06-frontend-spec.md` | Vue.js 3 component tree, state, API integration |
| `docs/07-testing-deployment.md` | Test plan, env setup, run/deploy instructions |
| `.claude/rules/coding-conventions.md` | Naming, error handling, style rules — applies to all code |
| `.env.example` | Required environment variables |

## Build Order (Phases)

1. **Phase 1 — Database & Backend Skeleton**: Postgres + pgvector setup, FastAPI app skeleton, document upload endpoint (storage only, no parsing yet).
2. **Phase 2 — Ingestion Pipeline**: Docling parsing, chunking, embedding generation, store chunks in DB. Async processing with status tracking.
3. **Phase 3 — RAG Query Pipeline**: Retrieval (pgvector similarity search), Groq API integration, citation extraction, `/api/chat` endpoint with streaming.
4. **Phase 4 — Frontend**: Vue 3 app — document manager, chat interface, citations, matching the mockup referenced in `docs/01-product-overview.md`.
5. **Phase 5 — Polish & Testing**: error states, loading states, test plan execution, deployment docs.

Each phase has its own checklist in `PROGRESS.md`. Don't start Phase N+1 until Phase N's checklist is complete, unless PROGRESS.md explicitly says to jump ahead.

## Non-Negotiable Constraints

- Embeddings are **384-dimensional** (`BAAI/bge-small-en-v1.5`). Don't swap embedding models without updating `docs/05-rag-pipeline-spec.md` and the DB schema together — they must stay in sync (vector column dimension is fixed at creation time in pgvector).
- Every chat answer that draws on document content **must** include a citation (filename + page number). No uncited claims presented as fact.
- Target retrieval latency: **under 1 second**. LLM generation time is separate — stream the response so it feels fast regardless.
- Backend: FastAPI (Python). Frontend: Vue.js 3 (Composition API). Database: PostgreSQL + pgvector. LLM: Groq API. Parsing: Docling. Don't substitute these without flagging it to the user first — they're fixed by the product brief.

## Tech Stack Quick Reference

- **Frontend**: Vue.js 3 (Composition API, `<script setup>`), Pinia, Vite
- **Backend**: FastAPI, Python 3.11+
- **Database**: PostgreSQL 15+ with the `pgvector` extension
- **Document parsing**: Docling
- **Embeddings**: `BAAI/bge-small-en-v1.5` via `sentence-transformers` (local inference, 384-dim)
- **LLM**: Groq API (fast Llama 3.x model — see `docs/05-rag-pipeline-spec.md` for the specific model string to verify at build time, since Groq's lineup changes)

## If You're Picking This Up Mid-Project

Orient yourself fast:

```bash
cat PROGRESS.md
git log --oneline -15   # if git is initialized
```

Then re-read only the spec doc for whatever phase PROGRESS.md says is active. Don't re-derive the whole architecture from scratch every session — that's exactly what this file and PROGRESS.md exist to prevent.
