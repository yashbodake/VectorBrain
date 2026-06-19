# 05 — RAG Pipeline Spec

This is the heart of the project. Two pipelines: **ingestion** (runs once per uploaded document) and **retrieval+generation** (runs once per chat question).

## Part A: Ingestion Pipeline

### Step 1 — Parse with Docling

```python
from docling.document_converter import DocumentConverter

converter = DocumentConverter()
result = converter.convert(file_path)
doc = result.document
```

Docling preserves document structure (headings, page boundaries, tables) far better than a raw text dump — this matters because citations depend on knowing which page a chunk came from.

Extract `page_count` from the parsed document here and update the `documents` row.

### Step 2 — Chunk with Docling's HybridChunker

Use Docling's built-in `HybridChunker` rather than a naive fixed-character splitter — it's tokenizer-aware and respects document structure (won't split mid-sentence/mid-table if it can help it).

```python
from docling.chunking import HybridChunker

chunker = HybridChunker(
    tokenizer="BAAI/bge-small-en-v1.5",  # match the embedding model's tokenizer
    max_tokens=512,
    merge_peers=True,
)
chunks = list(chunker.chunk(doc))
```

Target parameters:
- **Chunk size**: ~512 tokens max (tune down to ~300-400 if early testing shows retrieval pulling in too much irrelevant context per chunk)
- **Overlap**: HybridChunker handles this via peer-merging rather than a fixed overlap window; if a different chunker is used instead, use ~10% overlap (e.g., 50 tokens on a 512-token chunk)

For each chunk, extract:
- `content` — the chunk text
- `page_number` — from the chunk's provenance/metadata (Docling chunks carry source location info back to the original document — use it; don't re-derive page numbers heuristically)
- `chunk_index` — sequential order within the document

### Step 3 — Embed

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-small-en-v1.5")
embeddings = model.encode([c.content for c in chunks], normalize_embeddings=True)
```

- `normalize_embeddings=True` is important: pgvector's `<=>` cosine operator assumes this is consistent between ingestion and query time, and BGE models are trained/evaluated with normalized embeddings.
- Confirm `embeddings.shape[1] == 384` before writing to the DB — fail loudly (mark document `failed` with a clear `error_message`) if it doesn't match the schema's `vector(384)` column, rather than letting a silent dimension mismatch corrupt the table.
- BGE models recommend no special prefix for the *document/chunk* side (only the *query* side gets a prefix — see Part B). Don't add one here.

### Step 4 — Persist

Bulk-insert chunks with their embeddings, then flip `documents.status` to `'ready'` and set `processed_at`. On any exception in steps 1-3, set `status = 'failed'` with `error_message` populated — never leave a document stuck in `'processing'` indefinitely.

### Concurrency Note

Run ingestion as a FastAPI `BackgroundTasks` job (sufficient for Lite scope/single-user). If multiple large PDFs are uploaded back-to-back and this becomes a bottleneck, the natural upgrade is a proper task queue (e.g., Celery/RQ) — not needed for v1, but don't architect the ingestion function in a way that makes that swap painful (keep it as a single callable that takes a `document_id` and does the work, not tangled into the request handler).

---

## Part B: Retrieval + Generation Pipeline

### Step 1 — Embed the Query

```python
query_text = f"Represent this sentence for searching relevant passages: {question}"
query_embedding = model.encode(query_text, normalize_embeddings=True)
```

BGE models are trained to use an instruction prefix on the **query** side for retrieval tasks (asymmetric search) — this is the one place the embedding call differs from the ingestion side. Don't skip it; it measurably improves retrieval quality for BGE models.

### Step 2 — Similarity Search

Run the query from `docs/03-database-schema.md`:

```sql
SELECT c.content, c.page_number, d.filename, c.embedding <=> :query_embedding AS distance
FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE d.status = 'ready'
ORDER BY c.embedding <=> :query_embedding
LIMIT :top_k;
```

- **`top_k = 6`** as the starting default. Tune based on manual testing — too low and cross-document questions miss one of the relevant docs, too high and the prompt gets noisy and slower.
- **Relevance threshold**: cosine distance close to 0 means highly similar (1 means orthogonal, 2 means opposite, since these are normalized vectors). Treat results with `distance > 0.5` as too weak to be useful — if the top result is above that, treat retrieval as having found nothing relevant (see the "no relevant chunks" behavior in `docs/04-backend-api-spec.md`). Tune this threshold empirically once there's real content to test against; treat 0.5 as a starting point, not gospel.

### Step 3 — Build the Prompt

```
SYSTEM:
You are a study assistant. Answer the user's question using ONLY the document excerpts provided below.
If the excerpts don't contain enough information to answer, say so directly — do not guess or use outside knowledge.
Always ground your answer in the excerpts; the user will see citations alongside your answer, so don't repeat
"according to the document" type phrasing excessively — just answer naturally and accurately.

DOCUMENT EXCERPTS:
[1] (Physics_Textbook.pdf, p. 45): "Rockets work by expelling exhaust in the opposite direction of travel..."
[2] (Chemistry_Basics.pdf, p. 22): "Common rocket fuels include liquid hydrogen with liquid oxygen..."
...

USER QUESTION:
{question}
```

Number the excerpts so the model can implicitly anchor its answer to specific sources, but the **citations sent to the frontend** should come from which chunks were actually retrieved (Step 2), not from parsing the model's prose output — don't make citation correctness depend on the LLM correctly self-reporting what it used.

### Step 4 — Call Groq, Stream

```python
from groq import Groq

client = Groq(api_key=settings.GROQ_API_KEY)
stream = client.chat.completions.create(
    model="<verify current fast Llama model string in Groq's docs at build time>",
    messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": question}],
    stream=True,
)
for chunk in stream:
    token = chunk.choices[0].delta.content
    if token:
        yield token  # forward to SSE
```

Note: Groq's available model lineup changes over time — check `https://console.groq.com/docs/models` at implementation time rather than hardcoding a model string from this spec; pick the fastest currently-available Llama 3.x (or newer) instruction-tuned model.

### Step 5 — Emit Citations

After the stream completes, send the `done` SSE event with the de-duplicated list of `(filename, page_number)` from the chunks used in Step 2 (all top-k chunks that passed the relevance threshold — not just ones the model's prose happens to mention).

---

## Testing This Pipeline in Isolation

Before wiring up the full API, it's worth validating ingestion + retrieval as a standalone script:

1. Ingest 2-3 small test PDFs with known content
2. Manually query with a question you know the answer to, print the top-k chunks + distances
3. Confirm the right document/page shows up before plugging in the LLM call at all — if retrieval is bad, no amount of prompt tuning fixes it
