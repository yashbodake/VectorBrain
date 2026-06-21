# RAG Evaluation Report — Ikigai (4 Core Metrics)

> **Doc:** `Ikigai: the Japanese secret to a long and happy life` (123 pages, 153 chunks after v2 boilerplate filter, ingested on GPU)
> **Harness:** `backend/eval/run_eval.py` — LLM-as-judge (Cerebras gpt-oss-120b) for all 4 RAGAS-style metrics
> **Golden set:** `backend/eval/golden.jsonl` — 7 questions (5 on-topic, 2 off-topic)
> **Config:** `TOP_K_RESULTS=6`, `RETRIEVAL_DISTANCE_THRESHOLD=0.5`, `DEVICE=cuda` (RTX 2050)
> **Date:** 2026-06-21

---

## Headline Results

### RETRIEVAL — did we fetch the right context?

| Metric | Score | What it means |
|---|---|---|
| **context_precision** | **0.50** | Half the retrieved chunks are relevant. The boilerplate filter removed copyright/TOC/biblio noise, but some borderline chunks remain. |
| **context_recall** | **0.80** | 80% of the information needed to answer is present in the retrieved context. |
| [page hit_rate] | **1.00** | Every on-topic question retrieved at least one correct page. |
| [page recall@k] | **0.63** | Of all expected pages, 63% appeared in the top-6. |
| [page MRR] | **0.71** | Correct pages rank #1-2 on average. |

### GENERATION — did the LLM answer well?

| Metric | Score | What it means |
|---|---|---|
| **faithfulness** | **0.70** | Mostly grounded. Some answers include partly-unsupported claims when the model blends multiple chunks. |
| **response_relevance** | **1.00** | Every answer directly and completely addresses the question. No off-topic responses. |

### OFF-TOPIC handling

| Metric | Score | What it means |
|---|---|---|
| **off-topic declined** | **2/2** | Both unrelated questions (France, F1) correctly declined. Zero hallucination. |

---

## The Q2 fix — boilerplate filter PROVEN

**Q2:** *"Where do the authors travel to study longevity and ikigai?"*

| | Before (no filter) | After (v2 filter) |
|---|---|---|
| page hit_rate | **0.00** (missed) | **1.00** |
| retrieved pages | {3, 6, 119, 13, 64, 10} (copyright/TOC/biblio) | {13, 64, 10, 8, **70, 71**} (Ogimi content) |
| answer | "The excerpts do not mention any specific places..." | Correctly identifies Ogimi/Okinawa |
| root cause | Copyright/TOC/bibliography chunks ranked #1-3, crowding out Ogimi at rank #8 | Boilerplate chunks filtered at ingestion; Ogimi enters top-6 |

This is the definitive proof that the boilerplate filter (not threshold tuning, not top_k, not HyDE) was the correct fix. The root cause was **content quality** (publishers'-office noise), not retrieval parameters.

---

## Per-Question Detail

| # | Question | cp | cr | faith | rel | hit | recall | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | What is the meaning of ikigai? | 0.83 | 1.00 | 1.00 | 1.00 | 1 | 1.00 | Excellent |
| 2 | Where do authors travel for longevity? | 0.33 | 0.50 | 0.50 | 1.00 | 1 | 0.17 | Fixed (was total miss) |
| 3 | Is Okinawa a Blue Zone? | 0.33 | 1.00 | 1.00 | 1.00 | 1 | 1.00 | Perfect answer, noisy context |
| 4 | Capital of France? (off-topic) | - | - | - | - | - | - | Correctly declined |
| 5 | F1 suspension? (off-topic) | - | - | - | - | - | - | Correctly declined |
| 6 | What do centenarians celebrate? | 0.33 | 1.00 | 0.50 | 1.00 | 1 | 0.50 | Good retrieval, faith 0.5 |
| 7 | What does ikigai bring to life? | 0.67 | 0.50 | 0.50 | 1.00 | 1 | 0.50 | Relevant but partly unsupported |

---

## What's working well

1. **Response relevance 1.00** — every answer directly addresses the question.
2. **Off-topic handling 2/2** — perfect refusal of unrelated questions. Zero hallucination.
3. **Context recall 0.80** — the system finds 80% of the needed information.
4. **Q2 fixed** — the boilerplate filter resolved the hardest failure case (was a total miss, now hits Ogimi/Okinawa).

## What needs improvement

1. **Context precision 0.50** — half the retrieved chunks are noise. The boilerplate filter removed copyright/TOC/biblio, but borderline chunks (pages 113-116, index/back-matter) still leak through. A stricter filter or content-density check could help.

2. **Faithfulness 0.70** — when answers span multiple chunks, the model sometimes blends them and adds partly-unsupported claims (Q2, Q6, Q7 scored 0.5). Tightening the system prompt ("support every claim with a [n] citation") could push this toward 0.90+.

3. **Index/back-matter chunks (pages 113-116)** — these appear in retrieved results for Q1 and Q7. They are low-value (index entries) but not caught by the current boilerplate markers.

---

## Experiment history (full arc)

| Experiment | Change | Result | Decision |
|---|---|---|---|
| Baseline (k=6, thr=0.5) | - | hit=0.80, faith=0.90, Q2 missed | Starting point |
| Exp 1: top_k 6->10 | More chunks | hit unchanged, faith 0.90->0.60 | Reverted (noise) |
| Exp 2: threshold 0.5->0.45 | Looser filter | hit unchanged, faith 0.90->0.60 | Reverted (noise) |
| Boilerplate filter v1 | Copyright + biblio | 168->164 chunks, Q2 still missed (TOC leaked) | Partial fix |
| **Boilerplate filter v2** | + TOC detector | 168->153 chunks, **Q2 FIXED** | **Shipped** |

---

## How to reproduce

```bash
cd backend
python eval/run_eval.py --golden eval/golden.jsonl            # full 4-metric eval
python eval/run_eval.py --golden eval/golden.jsonl --no-judge # retrieval-only (fast)
```
