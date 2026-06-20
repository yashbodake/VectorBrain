# 08 — GPU Support & OCR-Fallback Plan

> **Status:** Plan (not yet implemented). Tracked in `PROGRESS.md` under "Open Questions / Decisions Needed".
> **Goal:** Make ingestion *optionally* use the local GPU (RTX 2050, 4GB) when available, behind an env switch — and fix the garbage-text problem on font-subsetted PDFs (e.g. NCERT) by forcing OCR when the embedded text layer is unusable.

## Context / why

Two independent problems surfaced:

1. **Everything runs on CPU today.** Phase 2 installed the CPU-only torch wheel (`2.12.1+cpu`) to dodge a torchvision ABI bug. The RTX 2050 sits idle. Big PDFs (the 282-page NCERT book) are slow to ingest because OCR runs on CPU.
2. **Some PDFs extract as garbage.** The NCERT Class 10 Science PDF uses font-subset/custom-encoding fonts; Docling trusts the broken embedded text layer and produces symbolic gibberish (`❱❿➀➂➃`, `/a113`, `<!-- formula-not-decoded -->`). Embeddings on gibberish → retrieval returns gibberish → the LLM correctly says "the excerpts don't contain the answer."

GPU makes #1 *faster*; it does **not** fix #2. OCR-fallback fixes #2 (independent of device). Both are worth doing; this plan addresses both.

## Verified facts (don't re-litigate these)

- **WSL2 exposes CUDA**: `/usr/lib/wsl/lib/libcuda.so.1` present; Windows driver 591.74 supports CUDA 12.4.
- **Matched cu124 pair exists for Python 3.13 (cp313)**: `torch 2.6.0+cu124` + `torchvision 0.21.0+cu124`. This is the ABI-safe combination (avoids the `torchvision::nms does not exist` trap from Phase 2).
- **Docling exposes `AcceleratorOptions`** with `device ∈ {'auto','cpu','cuda','mps','xpu'}` — GPU is a config switch, not a refactor.
- **`SentenceTransformer(model, device=...)`** accepts a device arg — one-line change.
- **4GB VRAM fits**: bge-small (~130MB) easily; RapidOCR PP-OCRv4 (~50MB) fits; Docling's table/formula models are tight and may OOM — keep them on CPU by default.

## Design principle

**Env-driven, auto by default, never break CPU.** A new `DEVICE` env var selects the device. `auto` (default) = use CUDA if torch was installed with it *and* it's available, else CPU. This keeps the current zero-GPU path working and makes GPU a turnkey upgrade — no code change, just a different torch wheel + (optionally) `DEVICE=cuda`.

---

## Part A — Env-driven device selection

### New env var

```ini
# .env / .env.example
# Compute device for ingestion (embeddings + Docling OCR/layout).
#   auto  — use CUDA if available, else CPU (default; safest)
#   cuda  — force CUDA (errors out if unavailable — fail loud, not silent-CPU)
#   cpu   — force CPU (current behavior)
DEVICE=auto
```

### `app/core/config.py`

Add a `DEVICE: Literal['auto','cpu','cuda'] = 'auto'` field, plus a **derived** `device` property that resolves `auto` to a concrete torch device at first use (cached):

```python
DEVICE: Literal['auto', 'cpu', 'cuda'] = 'auto'

@property
def torch_device(self) -> str:
    """Resolve DEVICE=auto to a concrete device, once. 'cuda' forces it
    (raises if unavailable — better to fail at boot than silently run on CPU
    when the user explicitly asked for GPU)."""
    import torch  # local import: don't load torch at config-import time
    if self.DEVICE == 'cpu':
        return 'cpu'
    if self.DEVICE == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError("DEVICE=cuda but torch.cuda.is_available() is False. "
                            "Install the cu124 torch wheel or set DEVICE=auto/cpu.")
    # auto
    return 'cuda' if torch.cuda.is_available() else 'cpu'
```

### `app/services/embeddings.py`

Pass the device into the model:

```python
def get_model() -> SentenceTransformer:
    return SentenceTransformer(settings.EMBEDDING_MODEL, device=settings.torch_device)
```

### `app/services/parsing.py`

Configure Docling's pipeline with `AcceleratorOptions`:

```python
from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions

@lru_cache(maxsize=1)
def _converter() -> DocumentConverter:
    device = AcceleratorDevice.CUDA if settings.torch_device == 'cuda' else AcceleratorDevice.CPU
    pipeline_options = PdfPipelineOptions(
        accelerator_options=AcceleratorOptions(device=device, num_threads=4),
        # do_ocr defaults True; see Part B for the auto-fallback.
    )
    return DocumentConverter(
        format_options={PdfFormatOption(pipeline_options=pipeline_options)}
    )
```

### Verification (Part A)

After installing the cu124 wheel and `DEVICE=auto`:
- `python -c "import torch; print(torch.cuda.is_available())"` → `True`.
- `from app.core.config import settings; print(settings.torch_device)` → `cuda`.
- Re-ingest a PDF; backend log should show `Using CUDA device` (RapidOCR logs the device), and ingestion is materially faster than CPU.

---

## Part B — OCR fallback for garbage-text PDFs (the NCERT fix)

**Problem:** Docling trusts the embedded text layer. For font-subsetted PDFs that layer is symbol soup. Forcing OCR (`do_ocr=True` with `force_full_ocr=True`) makes Docling rasterize the page and OCR it, bypassing the broken text.

### Two tiers

1. **Default (`OCR_FALLBACK=auto`)**: after parsing, detect if the text is garbage (see heuristic below); if so, automatically re-parse that document with `force_full_ocr=True`. One env var, no per-PDF action. Adds a second pass only when needed.

2. **Manual override (`OCR_FALLBACK=force`)**: always force OCR for every PDF. Slower but predictable for known-bad corpora.

3. **Off (`OCR_FALLBACK=off`)**: trust the text layer (current behavior).

```ini
# .env / .env.example
# OCR fallback when the PDF's embedded text is unusable (font-subsetted PDFs
# like NCERT extract as glyph garbage). See docs/08.
#   auto  — detect garbage text, re-OCR only those documents (default)
#   force — always OCR every page (slower, most robust for bad PDFs)
#   off   — trust the embedded text layer (current behavior)
OCR_FALLBACK=auto
```

### Garbage-detection heuristic (for `auto`)

Cheap, no model needed — score the extracted text per document:

- **Printable-ASCII / common-Unicode ratio**: garbage like `❱❿➀➂➃` and `/a113` is heavy on private-use / symbol blocks. Compute `printable_ratio = len(printable chars) / len(total non-whitespace)`.
- **Docling artifact density**: count occurrences of `formula-not-decoded` and `/aNNN` markers per KB.
- **Threshold**: if `printable_ratio < 0.6` OR artifact density > some per-KB threshold → mark as garbage, trigger re-OCR.

The exact thresholds get tuned against a small set (the working phonebook PDF = good; NCERT = bad). This is the kind of thing a golden eval set (see "how to evaluate RAG") would lock down.

### Where the logic lives

`app/services/ingestion.py`: after `parse_pdf`, before chunking, run `_is_text_garbage(doc)`. If garbage and `OCR_FALLBACK in {auto, force}`, call `parse_pdf(path, force_ocr=True)`. Wrapped in the existing try/except so a failure still flips the doc to `failed` with a readable error.

```python
doc_obj, page_count = parsing.parse_pdf(file_path)
if settings.OCR_FALLBACK in ("auto", "force"):
    needs_ocr = settings.OCR_FALLBACK == "force" or _is_text_garbage(doc_obj)
    if needs_ocr:
        logger.info("Re-OCR-ing %s (garbage text detected)", file_path)
        doc_obj, page_count = parsing.parse_pdf(file_path, force_ocr=True)
```

### `app/services/parsing.py` — add the `force_ocr` knob

```python
def parse_pdf(file_path: Path, force_ocr: bool = False) -> tuple[object, int]:
    pipeline_options = _pipeline_options(force_ocr=force_ocr)
    converter = _converter(force_ocr=force_ocr)  # cache per force_ocr
    ...
```

`PdfPipelineOptions.ocr_options.force_full_ocr = True` + `do_ocr=True` forces the rasterize+OCR path.

### Verification (Part B)

- Re-ingest the NCERT PDF with `OCR_FALLBACK=auto` → backend log shows "Re-OCR-ing"; resulting chunks are **readable English** (chapter titles, body text), not `❱❿➀`.
- Ask "what is the first chapter?" → retrieval finds the actual TOC/chapter-1 page → the LLM answers "Chemical Reactions and Equations" with a citation.
- Re-ingest the phonebook PDF (already clean) → **no** re-OCR triggered (garbage detector says it's fine), so no wasted work.

---

## Part C — Install / dependency story

The GPU path needs different wheels than the current CPU setup. Both must stay reproducible.

### `pyproject.toml` — torch becomes environment-specific

Keep the **source** constraints loose (`torch>=2.5`, `torchvision>=0.20`, `transformers>=4.41,<5`) — they're satisfied by either wheel. The **device** is chosen at *install* time, documented in the README, not encoded in `pyproject.toml`:

```bash
# CPU (current default — works everywhere)
pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cpu

# GPU (CUDA 12.4 — RTX 2050 / WSL2)
pip install torch==2.6.0+cu124 torchvision==0.21.0+cu124 --index-url https://download.pytorch.org/whl/cu124
```

Pin **exact versions** (2.6.0 / 0.21.0) to guarantee the ABI-matched pair — the Phase 2 bug was an *unmatched* pair.

### New Makefile / scripts (optional, nice-to-have)

```bash
make install-cpu   # cpu wheels
make install-gpu   # cu124 wheels + sets DEVICE=auto in .env
```

Not strictly required; the README commands suffice.

---

## Out of scope (deliberately)

- **No local LLM.** Cerebras stays the API; the 2050's 4GB can't run a useful answer-generation model anyway. GPU is for ingestion only.
- **No multi-GPU / sharding.** Single 4GB card; Docling's table/formula models stay on CPU to avoid OOM. If a later user has a bigger GPU, the same `DEVICE=cuda` switch picks it up.
- **No persistent re-OCR flag on the document row.** Re-OCR is a parse-time decision based on the env + the garbage detector. (Could add a `force_ocr` per-upload param later if wanted — not now.)
- **No changing the embedding model.** bge-small stays; it's the 384-dim contract with the DB schema. GPU just runs it faster.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| 4GB OOM during Docling full pipeline | Default keeps table/formula models on CPU; monitor VRAM; wrap parse in try/except → doc `failed`. |
| cu124 wheel conflicts with other deps | Install in a **fresh** venv, run the test suite (27 tests) before trusting it. Keep CPU venv as fallback. |
| Garbage detector misfires (re-OCRs a clean PDF, wasting time) | Conservative threshold + log every decision; `OCR_FALLBACK=off` escape hatch. |
| WSL2 CUDA flakiness | `DEVICE=auto` falls back to CPU transparently; `DEVICE=cuda` fails loud so you know. |

---

## Implementation order (when you say go)

1. **Part A config + wiring** (config.py, embeddings.py, parsing.py) — small, safe, works on CPU immediately (`auto` → CPU when no CUDA).
2. **Install cu124 wheels in a fresh venv**, verify `torch.cuda.is_available() == True` and all 27 tests still pass.
3. **Bench it**: re-ingest NCERT on CPU vs GPU, log times.
4. **Part B OCR fallback** — config + garbage detector + ingestion wiring.
5. **Re-ingest NCERT with `OCR_FALLBACK=auto` + GPU**, verify readable chunks + correct "first chapter" answer.
6. Update `.env.example`, README (CPU vs GPU install sections), `PROGRESS.md` (log as deviation + tick off this decision).

Each step is independently revertible (it's all behind env switches + a fresh venv).

## Open question for the user

**Which to do first — Part A (GPU) or Part B (OCR fix)?**
- B fixes the *actual bug* you hit (NCERT garbage) and is lower-risk (no new wheels, no OOM).
- A makes everything *faster* but doesn't fix correctness.
My recommendation: **B first** (fix correctness), **A second** (speed). Confirm and I'll implement.
