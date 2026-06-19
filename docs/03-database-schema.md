# 03 — Database Schema

## Extension

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

## Tables

### `documents`

Tracks each uploaded PDF and its processing status.

```sql
CREATE TABLE documents (
    id              SERIAL PRIMARY KEY,
    filename        VARCHAR(255) NOT NULL,
    file_path       TEXT NOT NULL,              -- where the raw PDF is stored on disk/object storage
    page_count      INTEGER,
    file_size_bytes BIGINT,
    status          VARCHAR(20) NOT NULL DEFAULT 'uploaded',
                    -- one of: 'uploaded', 'processing', 'ready', 'failed'
    error_message   TEXT,                        -- populated only if status = 'failed'
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ                  -- set when status becomes 'ready' or 'failed'
);
```

### `chunks`

Stores each chunk of parsed text plus its embedding.

```sql
CREATE TABLE chunks (
    id            SERIAL PRIMARY KEY,
    document_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content       TEXT NOT NULL,
    page_number   INTEGER,                       -- nullable: some chunks may span pages or page info may be unavailable
    chunk_index   INTEGER NOT NULL,               -- order within the document, 0-based
    embedding     vector(384) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`ON DELETE CASCADE` matters: deleting a document via `DELETE /api/documents/{id}` should remove its chunks automatically.

### Indexes

```sql
-- Vector similarity index. Use HNSW if your pgvector version supports it (>=0.5.0);
-- it has better recall/speed tradeoffs than IVFFlat for this dataset size and doesn't
-- need a separate ANALYZE/training step before it's useful.
CREATE INDEX chunks_embedding_idx
    ON chunks
    USING hnsw (embedding vector_cosine_ops);

-- Fallback if HNSW isn't available in your pgvector version:
-- CREATE INDEX chunks_embedding_idx ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX chunks_document_id_idx ON chunks(document_id);
CREATE INDEX documents_status_idx ON documents(status);
```

## Example Query: Top-K Similarity Search

This is the core retrieval query, restricted to documents that have finished processing:

```sql
SELECT
    c.id,
    c.content,
    c.page_number,
    d.filename,
    c.embedding <=> :query_embedding AS distance
FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE d.status = 'ready'
ORDER BY c.embedding <=> :query_embedding
LIMIT :top_k;
```

`<=>` is pgvector's cosine distance operator (lower = more similar). Bind `:query_embedding` as a 384-length vector literal and `:top_k` per `docs/05-rag-pipeline-spec.md`.

## Migrations

Use **Alembic** for migrations rather than hand-running DDL, even for a Lite project — it's one `pip install` and saves pain the moment the schema needs to change mid-build.

```bash
pip install alembic
alembic init alembic
# configure alembic.ini + env.py to point at DATABASE_URL from .env
alembic revision -m "initial schema" --autogenerate
alembic upgrade head
```

Keep the two table definitions above as the source of truth; if you hand-edit the DB during development, regenerate a migration afterward so `alembic upgrade head` from a clean database still reproduces the real schema.

## Notes for Implementation

- `page_number` can be `NULL` if Docling can't attribute a chunk to a single page (e.g., a chunk spanning a page break) — handle this gracefully in citation display rather than crashing (`docs/06-frontend-spec.md` should show "page unknown" or omit the page number in that case).
- Don't store the embedding model name on each chunk row for Lite scope — there's only one model in use (`bge-small-en-v1.5`, 384-dim) and the column dimension already enforces it. If you ever support swapping embedding models, that's a schema migration, not a runtime branch.
- `file_path` should point to wherever raw PDFs are stored (local disk under e.g. `./storage/documents/{id}.pdf` is fine for Lite scope; no need for S3 unless deploying somewhere without persistent disk).
