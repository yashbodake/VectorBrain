# 01 — Product Overview

## Core Objective

Build a "second brain" for studying. The user uploads multiple PDFs and asks questions across all of them at once. Answers are generated with RAG (Retrieval-Augmented Generation) and grounded with page-level citations back to the source document.

This is the **Lite** scope: a focused, personal-project-sized build (roughly a 3-day implementation), not a production multi-tenant SaaS. Single user, no auth, no billing, no team sharing — those are explicitly out of scope (see below).

## Target User

A student or self-learner who has a stack of PDFs (textbooks, papers, notes) and wants to ask questions that span across all of them instead of searching each file manually.

## Key Features (In Scope)

1. **Upload & manage multiple documents** — drag/select multiple PDFs, see them listed with page count and processing status.
2. **Query across all uploaded documents** — one chat box, answers can pull from any/all ready documents, not scoped to a single file.
3. **Citations** — every answer shows which document (and ideally page) it came from.
4. **Streaming answers** — tokens appear as they're generated, not as one blocking response.
5. **Reactive front-end** — Vue.js 3, so document list and chat state update live as processing completes.

## Out of Scope (Lite)

Do not build these unless the user explicitly asks to extend scope later:

- User accounts / authentication / multi-user support
- Per-document permissions or sharing
- Editing or annotating PDFs in-app
- Document formats other than PDF (no .docx, .pptx, .txt ingestion in v1)
- Persistent multi-session chat history / conversation management UI (a single running chat session is enough)
- Mobile app (responsive web is enough)
- Fine-tuning or training custom models

## Definition of Done

The Lite build is considered done when all of the following hold:

- [ ] User can upload multiple PDFs through the UI
- [ ] User can ask a question and get an answer that draws from **all** uploaded documents, not just one
- [ ] Answers cite the source document (filename) and page number
- [ ] Retrieval (the vector search step) responds in under ~1 second; full answer streams in rather than blocking
- [ ] Data persists in PostgreSQL with `pgvector` — documents and their chunk embeddings survive a server restart
- [ ] Frontend is a working Vue.js 3 app reflecting document and chat state reactively

## Tech Stack (Fixed)

| Layer | Choice | Why |
|---|---|---|
| Frontend | Vue.js 3 (Composition API) | Reactive, component-based, suits managing multiple documents + streaming chat state |
| Backend | FastAPI | Async-friendly, fast to build, good fit for streaming endpoints |
| Database | PostgreSQL + pgvector | One database for both relational metadata and vector search — no separate vector DB to operate |
| Document parsing | Docling | Structure-aware PDF parsing (keeps page boundaries, headings, tables) |
| Embeddings | `BAAI/bge-small-en-v1.5` (384-dim) | Local, fast, no API cost, good quality at small size |
| LLM | Groq API | Very low-latency inference, fits the "answers feel instant" goal |

See `docs/02-architecture.md` for how these fit together, and `docs/05-rag-pipeline-spec.md` for the ingestion/retrieval details.

## Reference Mockup (for UI fidelity)

The frontend should match the spirit of this layout:

- Left panel: "Uploaded Documents" list, each entry shows filename, page count, file size, a status indicator (uploading / processing / ready / failed), and a way to add more (`+ Add`)
- Right panel: chat thread — past Q&A pairs, each answer followed by a small citation line (e.g., "Source: Physics_Textbook.pdf, p. 45"), with a text input + send button pinned at the bottom labeled something like "Ask a question across all documents..."

Exact visual styling (colors, spacing, fonts) is up to whoever implements `docs/06-frontend-spec.md` — the structural layout above is the part that matters for the product to make sense.
