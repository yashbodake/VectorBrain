# 04 — Backend API Spec

Base URL (local dev): `http://localhost:8000/api`

All responses are JSON unless noted (the chat endpoint streams via SSE).

---

## `POST /api/documents`

Upload a new PDF.

**Request:** `multipart/form-data`, field name `file`

**Response (201):**
```json
{
  "id": 1,
  "filename": "Physics_Textbook.pdf",
  "page_count": null,
  "file_size_bytes": 4404019,
  "status": "uploaded",
  "uploaded_at": "2026-06-19T10:00:00Z"
}
```

Behavior:
- Validate file is a PDF (content-type and/or magic bytes); reject with `400` otherwise.
- Validate file size against `MAX_UPLOAD_SIZE_MB` (see `.env.example`); reject with `413` if exceeded.
- Save the raw file to disk, insert the `documents` row, **kick off the ingestion pipeline as a background task**, and return immediately — do not block the HTTP response on Docling parsing or embedding generation.
- `page_count` is `null` until Docling finishes parsing; it gets filled in during ingestion.

**Errors:**
- `400` — not a valid PDF
- `413` — file too large

---

## `GET /api/documents`

List all documents.

**Response (200):**
```json
{
  "documents": [
    {
      "id": 1,
      "filename": "Physics_Textbook.pdf",
      "page_count": 245,
      "file_size_bytes": 4404019,
      "status": "ready",
      "uploaded_at": "2026-06-19T10:00:00Z",
      "processed_at": "2026-06-19T10:00:42Z"
    },
    {
      "id": 2,
      "filename": "Chemistry_Basics.pdf",
      "page_count": null,
      "file_size_bytes": 3250176,
      "status": "processing",
      "uploaded_at": "2026-06-19T10:05:00Z",
      "processed_at": null
    }
  ]
}
```

Used by the frontend to render the document list and poll for status changes (see `docs/06-frontend-spec.md` for polling cadence).

---

## `GET /api/documents/{id}`

Get a single document's current state (lighter-weight than the list, useful for targeted polling while one document is still processing).

**Response (200):** same shape as one item in the list above, plus `error_message` if `status == "failed"`.

**Errors:**
- `404` — no document with that ID

---

## `DELETE /api/documents/{id}`

Remove a document and everything derived from it.

**Response (204):** no body.

Behavior:
- Delete the `documents` row (cascades to `chunks` per the FK constraint)
- Delete the raw file from disk
- Idempotent-ish: if the ID doesn't exist, return `404` rather than silently succeeding, so the frontend can surface a real error if it's out of sync.

**Errors:**
- `404` — no document with that ID

---

## `POST /api/chat`

Ask a question across all `ready` documents. Streams the answer.

**Request:**
```json
{ "question": "What are the chemical components of rocket fuel?" }
```

**Response:** `text/event-stream` (SSE). Event sequence:

1. Zero or more `token` events as the answer streams:
   ```
   event: token
   data: {"text": "Common "}
   ```
2. Exactly one final `done` event carrying the citations actually used:
   ```
   event: done
   data: {"citations": [{"filename": "Chemistry_Basics.pdf", "page_number": 22}, {"filename": "Physics_Textbook.pdf", "page_number": 45}]}
   ```
3. If something fails mid-stream, an `error` event instead of `done`:
   ```
   event: error
   data: {"message": "LLM provider error, please retry"}
   ```

Behavior:
- If retrieval returns no chunks above a reasonable similarity threshold (see `docs/05-rag-pipeline-spec.md`), don't call the LLM with an empty/weak context and let it hallucinate — stream back a direct response such as "I couldn't find anything relevant in your uploaded documents for that," with an empty `citations` array, and skip the Groq call entirely.
- `citations` in the `done` event should be de-duplicated (same filename+page cited from multiple chunks → one entry) and ideally ordered by relevance.
- If there are zero documents with `status = 'ready'`, return a normal `done` event explaining that no documents are ready yet, rather than erroring.

**Errors:**
- `400` — empty/missing `question`
- `503` — Groq API unreachable (surface this distinctly from "no relevant chunks," since the user's fix is different — retry later vs. rephrase)

---

## Cross-Cutting Conventions

- All timestamps are ISO-8601 UTC.
- Error responses use a consistent shape: `{"detail": "human readable message"}` (FastAPI's default `HTTPException` shape — don't invent a custom error envelope).
- CORS: allow the Vite dev server origin (`http://localhost:5173` by default) in development; restrict to the actual deployed frontend origin in production.
- No authentication in Lite scope (see `docs/01-product-overview.md` — out of scope). Don't add auth scaffolding unless asked.
