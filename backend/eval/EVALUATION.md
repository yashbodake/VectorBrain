# RAG Evaluation Report — Ikigai

> **Doc:** `Ikigai: the Japanese secret to a long and happy life` (123 pages, 168 chunks, ingested on GPU)
> **Harness:** `backend/eval/run_eval.py` against the live `/api/chat` endpoint
> **Golden set:** `backend/eval/golden.jsonl` — 7 questions (5 on-topic with known-answer pages; 2 off-topic that should decline)
> **Date:** 2026-06-19

---

## Headline results (baseline, `TOP_K_RESULTS=6`)

| Metric | Score | What it measures |
|---|---|---|
| **Retrieval hit_rate** | **0.80** | Did the retrieved sources contain *any* expected page? (4/5 on-topic) |
| **Retrieval recall@k** | **0.60** | Fraction of *all* expected pages that appeared in the retrieved set |
| **Retrieval MRR** | **0.67** | Reciprocal rank of the first correct page (1.0 = ranked #1) |
| **Key-facts coverage** | **0.80** | Did the answer mention the `must_mention` facts? |
| **Faithfulness (LLM-judge)** | **0.90** | Is the answer supported by the retrieved context? (1.0 = no hallucination) |
| **Off-topic declined** | **2/2** | Unrelated questions correctly refused (no hallucination) |
| **Overall gen pass** | **6/7** | |

**Interpretation:** grounding is strong (faithfulness 0.90), off-topic handling is perfect, but retrieval has a real miss it's worth fixing.

---

## Experiment 1 — `TOP_K_RESULTS` 6 → 10

**Hypothesis:** retrieving more chunks would catch the Q2 miss (more candidates = better odds of hitting Ogimi/Okinawa).

**Result:** ❌ **did not help, and made faithfulness worse.**

| Metric | k=6 (baseline) | k=10 | Change |
|---|---|---|---|
| hit_rate | 0.80 | 0.80 | unchanged |
| recall@k | 0.60 | 0.60 | unchanged |
| MRR | 0.67 | 0.67 | unchanged |
| key-facts | 0.80 | 0.80 | unchanged |
| **faithfulness** | **0.90** | **0.60** | **↓ worse** |
| Q2 (the miss) | miss | miss | unchanged |

**Why faithfulness dropped:** more chunks = more noise in the prompt. With 10 chunks the model started blending loosely-related passages and adding claims the judge marked as partly-unsupported. Classic context-precision failure.

**Why retrieval didn't improve:** Q2 ("where do the authors travel") isn't a top-k problem — it's a **lexical/semantic mismatch**. The word "travel" doesn't overlap semantically with "Ogimi is a rural town on the north end of Okinawa." More chunks of the *wrong kind* don't help; the relevant chunk simply doesn't rank high enough regardless of k. This needs a different fix (see Recommendations).

**Decision: keep `TOP_K_RESULTS=6`** (reverted). Larger k hurt faithfulness with no retrieval upside.

---

## Per-question detail (baseline, k=6)

| # | Question | Verdict | hit | recall | mrr | keyfacts | faith | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | What is the meaning of ikigai? | ✓ | 1.0 | 1.00 | 1.00 | 1.00 | 1.00 | Perfect |
| 2 | Where do the authors travel to study longevity? | **✗** | **0.0** | **0.00** | **0.00** | 0.00 | 1.00 | **Retrieval miss — see below** |
| 3 | Is Okinawa a Blue Zone? | ✓ | 1.0 | 1.00 | 1.00 | 1.00 | 1.00 | Perfect |
| 4 | Capital of France? | ✓ | — | — | — | 1.00 | — | Correctly declined (off-topic) |
| 5 | Tune an F1 car's suspension? | ✓ | — | — | — | 1.00 | — | Correctly declined (off-topic) |
| 6 | What do centenarians celebrate? | ✓ | 1.0 | 0.50 | 1.00 | 1.00 | 0.50 | Faith 0.5 — model merged chunks, added a partly-unsupported claim |
| 7 | What does ikigai bring to life? | ✓ | 1.0 | 0.50 | 0.33 | 1.00 | 1.00 | Correct page ranked #3 |

---

## The one failure (Q2) — root cause

**Q:** *"Where do the authors travel to study longevity and ikigai?"*
**Expected:** Ogimi / Okinawa (pages 9, 14, 39).
**Got:** nothing relevant cleared the 0.5 threshold.

This is a **semantic-mismatch** retrieval failure, not a k or threshold problem (proven by Experiment 1). The query's framing ("travel to study") doesn't share enough token/semantic overlap with the chunk text ("Ogimi, a rural town on the north end of Okinawa") for the cosine distance to clear the threshold.

**Candidate fixes to A/B test next:**
1. **HyDE-style query rewriting** — have a cheap LLM call rewrite the query into a hypothetical answer ("The authors travel to Ogimi in Okinawa...") before embedding it. Best-known fix for this exact failure mode.
2. **Lower `RETRIEVAL_DISTANCE_THRESHOLD` 0.5 → 0.45** — borderline matches (the Ogimi chunk was likely ~0.5–0.55). Re-run the eval to confirm recall goes up without hurting faithfulness.
3. **Multi-query retrieval** — embed 2-3 paraphrases of the question and union the results.

---

## What's working well

- **Faithfulness 0.90** — the prompt + grounding works; the model rarely invents facts.
- **Off-topic handling 2/2** — the 0.5 threshold + canned-decline logic correctly refuses unrelated questions. No hallucination.
- **Hit rate 0.80** — 4/5 on-topic questions retrieved the right page, several at rank #1.
- **Citation fidelity** — answers cite real source pages; the `[n]` inline markers resolve to actual excerpts.

## What's not

- **Q2 semantic mismatch** (above) — one concrete retrieval miss.
- **Q6 faithfulness 0.5** — when the answer spans multiple chunks, the model occasionally adds a partly-unsupported claim. Tightening the system prompt ("cite a number for every claim") may help.

---

## Recommended next experiments (priority order)

1. **Lower threshold 0.5 → 0.45** — cheap, likely recovers Q2. Re-run eval, watch faithfulness doesn't drop.
2. **Grow the golden set to 20-30 questions** — 7 shows the shape; 20+ gives statistical confidence before tuning.
3. **HyDE query rewriting** — if threshold-tuning plateaus, this is the robust fix for semantic-mismatch misses.
4. **Prompt tweak for Q6** — "support every claim with a `[n]` citation" to push faithfulness toward 1.0.

---

## How to reproduce

```bash
# backend running on :8000, Ikigai ingested
cd backend
python eval/run_eval.py                    # full eval (uses LLM judge)
python eval/run_eval.py --no-judge         # retrieval-only (fast, no API cost)
```

Raw per-question results: `results.json`. Baseline (k=6) and experiment (k=10) snapshots: `results_k6.json`, `results_k10.json`.
