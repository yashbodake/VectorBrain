"""RAG evaluation harness for VectorBrain.

Runs a golden question set against the live /api/chat endpoint and scores:
  RETRIEVAL (independent of the answer text):
    - hit_rate: did ANY retrieved source come from an expected page?
    - recall@k: fraction of expected pages that appeared in retrieved sources
    - mrr: reciprocal rank of the first expected-page hit (1/rank)
  GENERATION:
    - faithfulness: does the answer only use facts present in the retrieved
      context? (LLM-as-judge via Cerebras)
    - contains_key_facts: did the answer mention the must_mention terms?
    - declined_correctly: for off-topic questions (expected_pages=[]), did the
      system decline rather than hallucinate? (counts as a PASS for those rows)

Usage:
    # backend running on :8000 with at least one doc ready
    python eval/run_eval.py
    python eval/run_eval.py --api http://localhost:8000 --golden eval/golden.jsonl

The golden set is eval/golden.jsonl: one JSON object per line with
question, expected_pages (list of page numbers that contain the answer),
reference_facts, and must_mention (lowercase tokens the answer should contain).

Design notes:
- Retrieval is scored from the sources/citations in the SSE done-event, NOT by
  re-embedding, so it measures the real retrieval the user sees.
- Faithfulness uses Cerebras (the configured LLM) as a judge — mock it by
  setting --no-judge for a retrieval-only run if you don't want to spend calls.
- This is a Lite eval (handful of questions, LLM-judge), not RAGAS/TruLens —
  see the "how to evaluate RAG" notes. Good enough to tune top_k/threshold.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

API_DEFAULT = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Golden set loading
# ---------------------------------------------------------------------------
def load_golden(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Hit the live /api/chat and capture: tokens, citations (per-chunk), sources (deduped)
# ---------------------------------------------------------------------------
def run_query(api: str, question: str, document_ids: list[int] | None = None) -> dict:
    body = {"question": question}
    if document_ids:
        body["document_ids"] = document_ids
    req = urllib.request.Request(
        f"{api}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    tokens, citations, sources, error = [], [], [], None
    with urllib.request.urlopen(req, timeout=120) as resp:
        event = None
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                try:
                    payload = json.loads(line.split(":", 1)[1].strip())
                except json.JSONDecodeError:
                    continue
                if event == "token":
                    tokens.append(payload.get("text", ""))
                elif event == "done":
                    citations = payload.get("citations", [])
                    sources = payload.get("sources", [])
                elif event == "error":
                    error = payload.get("message")
    return {"answer": "".join(tokens), "citations": citations, "sources": sources, "error": error}


# ---------------------------------------------------------------------------
# Retrieval scoring (from the sources array — deduped per page)
# ---------------------------------------------------------------------------
def score_retrieval(sources: list[dict], expected_pages: set[int]) -> dict:
    if not expected_pages:
        # Off-topic question: retrieval "should" be empty. If it is, that's a
        # perfect retrieval result (didn't fetch junk). If not, it fetched noise.
        empty = len(sources) == 0
        return {"hit": 1.0 if empty else 0.0, "recall": 1.0 if empty else 0.0, "mrr": 1.0 if empty else 0.0}
    retrieved_pages = [s.get("page_number") for s in sources if s.get("page_number") is not None]
    # hit: any expected page appeared at all
    hits = [p for p in retrieved_pages if p in expected_pages]
    hit = 1.0 if hits else 0.0
    # recall: fraction of expected pages covered (cap at the k we retrieved)
    recall = len(set(hits)) / len(expected_pages)
    # mrr: 1/rank of the first expected-page hit
    mrr = 0.0
    for rank, p in enumerate(retrieved_pages, start=1):
        if p in expected_pages:
            mrr = 1.0 / rank
            break
    return {"hit": hit, "recall": recall, "mrr": mrr, "retrieved_pages": retrieved_pages}


# ---------------------------------------------------------------------------
# Generation scoring
# ---------------------------------------------------------------------------
def score_contains_key_facts(answer: str, must_mention: list[str]) -> float:
    """Fraction of must_mention lowercase tokens present in the answer."""
    if not must_mention:
        return 1.0
    low = answer.lower()
    present = sum(1 for term in must_mention if term.lower() in low)
    return present / len(must_mention)


def is_decline(answer: str) -> bool:
    """Did the system give a canned 'couldn't find anything' decline?"""
    low = answer.lower()
    return ("couldn't find" in low) or ("don't contain" in low) or ("not contain" in low) or ("no documents are ready" in low)


def faithfulness_judge(api: str, question: str, answer: str, context_chunks: list[str]) -> tuple[float, str]:
    """LLM-as-judge: does the answer only use facts present in the context?

    Returns (score 0..1, reasoning). Uses Cerebras via the app's own config —
    but to avoid importing the app (heavy), we call the judge model through the
    same OpenAI-compatible endpoint configured in the env.

    Score: 1.0 = fully supported, 0.5 = partly, 0.0 = hallucinated/unsupported.
    """
    # Read config the same way the app does (but without importing app).
    import os
    from openai import OpenAI

    cfg = _load_env()
    client = OpenAI(api_key=cfg["CEREBRAS_API_KEY"], base_url=cfg["CEREBRAS_BASE_URL"])
    context = "\n\n".join(f"[{i+1}] {c[:600]}" for i, c in enumerate(context_chunks[:6])) or "(no context retrieved)"
    prompt = (
        "You are an evaluator. Judge whether the ANSWER is supported by the CONTEXT only.\n"
        "Return EXACTLY one line: SCORE|REASON, where SCORE is 1.0 (fully supported), "
        "0.5 (partly supported), or 0.0 (mostly unsupported / hallucinated).\n\n"
        f"QUESTION: {question}\n\nCONTEXT:\n{context}\n\nANSWER: {answer}\n\nJudge:"
    )
    try:
        resp = client.chat.completions.create(
            model=cfg["CEREBRAS_MODEL"],
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            temperature=0.0,
        )
        out = (resp.choices[0].message.content or "").strip()
        m = re.match(r"\s*([01](?:\.\d+)?)\s*[|]\s*(.+)", out)
        if m:
            return float(m.group(1)), m.group(2)
        return 0.5, f"unparseable judge output: {out[:120]}"
    except Exception as e:  # noqa: BLE001
        return -1.0, f"judge error: {e}"


def _load_env() -> dict:
    """Load just the Cerebras config from the repo .env (no app import)."""
    env = Path(__file__).resolve().parents[2] / ".env"
    cfg = {}
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return {
        "CEREBRAS_API_KEY": cfg.get("CEREBRAS_API_KEY", ""),
        "CEREBRAS_BASE_URL": cfg.get("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1"),
        "CEREBRAS_MODEL": cfg.get("CEREBRAS_MODEL", "gpt-oss-120b"),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=API_DEFAULT)
    ap.add_argument("--golden", default=str(Path(__file__).parent / "golden.jsonl"))
    ap.add_argument("--no-judge", action="store_true", help="skip LLM faithfulness judge")
    args = ap.parse_args()

    golden = load_golden(Path(args.golden))
    print(f"\n=== RAG eval: {len(golden)} questions vs {args.api} ===\n")

    rows = []
    for i, item in enumerate(golden, 1):
        q = item["question"]
        expected = set(item.get("expected_pages", []))
        print(f"[{i}/{len(golden)}] Q: {q}")
        result = run_query(args.api, q)
        answer = result["answer"]
        sources = result["sources"]
        ret = score_retrieval(sources, expected)
        key_facts = score_contains_key_facts(answer, item.get("must_mention", []))
        declined = is_decline(answer)

        # Generation correctness for off-topic: declining IS the right answer.
        gen_pass = None
        if not expected:
            gen_pass = declined  # should decline
        else:
            # On-topic: must not decline AND should contain key facts.
            gen_pass = (not declined) and (key_facts >= 0.5)

        faith, faith_reason = (-1.0, "skipped (--no-judge)")
        if not args.no_judge and expected and not declined:
            # Use the per-chunk citations' excerpts as the judge's context.
            ctx = [c.get("excerpt", c.get("content", "")) for c in result["citations"]]
            faith, faith_reason = faithfulness_judge(args.api, q, answer, ctx)

        row = {
            "question": q,
            "expected_pages": sorted(expected),
            "retrieved_pages": ret.get("retrieved_pages", []),
            "retrieval_hit": ret["hit"],
            "retrieval_recall": ret["recall"],
            "retrieval_mrr": ret["mrr"],
            "key_facts": round(key_facts, 2),
            "declined": declined,
            "gen_pass": gen_pass,
            "faithfulness": round(faith, 2),
            "faith_reason": faith_reason[:140],
            "answer_preview": answer[:160].replace("\n", " "),
            "error": result["error"],
        }
        rows.append(row)
        status = "✓" if (gen_pass and ret["hit"] > 0 if expected else gen_pass) else "✗"
        print(f"   {status} ret_hit={ret['hit']:.0f} recall={ret['recall']:.2f} mrr={ret['mrr']:.2f} "
              f"keyfacts={key_facts:.2f} declined={declined} faith={faith:.2f}")
        if result["error"]:
            print(f"      error: {result['error']}")

    # Aggregate
    n = len(rows)
    on_topic = [r for r in rows if r["expected_pages"]]
    off_topic = [r for r in rows if not r["expected_pages"]]

    def avg(xs, key):
        xs = [x for x in xs if x[key] >= 0]
        return sum(x[key] for x in xs) / len(xs) if xs else 0.0

    print("\n=== SUMMARY ===")
    print(f"  retrieval hit_rate  : {avg(on_topic,'retrieval_hit'):.2f}  (on-topic n={len(on_topic)})")
    print(f"  retrieval recall@k  : {avg(on_topic,'retrieval_recall'):.2f}")
    print(f"  retrieval MRR       : {avg(on_topic,'retrieval_mrr'):.2f}")
    print(f"  key-facts coverage  : {avg(on_topic,'key_facts'):.2f}")
    judged = [r for r in on_topic if r["faithfulness"] >= 0]
    if judged:
        print(f"  faithfulness (LLM)  : {avg(judged,'faithfulness'):.2f}  (n={len(judged)})")
    print(f"  off-topic declined  : {sum(r['gen_pass'] for r in off_topic)}/{len(off_topic)} (should decline)")
    gen_correct = sum(1 for r in rows if r["gen_pass"])
    print(f"  overall gen pass    : {gen_correct}/{n}")

    # Write detailed results
    out = Path(__file__).parent / "results.json"
    out.write_text(json.dumps({"summary": {
        "hit_rate": avg(on_topic, "retrieval_hit"),
        "recall_at_k": avg(on_topic, "retrieval_recall"),
        "mrr": avg(on_topic, "retrieval_mrr"),
        "key_facts": avg(on_topic, "key_facts"),
        "faithfulness": avg(judged, "faithfulness") if judged else None,
        "off_topic_declined": sum(r["gen_pass"] for r in off_topic) if off_topic else None,
        "overall_gen_pass": gen_correct,
        "n": n,
    }, "rows": rows}, indent=2))
    print(f"\n  detailed results -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
