"""RAG evaluation harness for VectorBrain — the 4 core RAGAS-style metrics.

Scores both halves of the RAG pipeline independently (so a bad score tells you
WHICH stage to fix):

  RETRIEVAL (did we fetch the right context?):
    - context_precision : of the retrieved chunks, how many are actually
                          relevant to answering the question? (penalizes noise)
    - context_recall    : of the information needed to answer (from the
                          reference answer), how much is present in the
                          retrieved context? (penalizes missing the answer)

  GENERATION (did the LLM produce a good answer from that context?):
    - faithfulness      : is every claim in the answer supported by the
                          retrieved context? (1.0 = no hallucination)
    - response_relevance: does the answer actually address the question?
                          (1.0 = fully on-topic; penalizes vague/off-topic)

All four are computed via an LLM-as-judge (Cerebras, via the app's config).
Retrieval also gets page-level hit_rate/recall/MRR for free (deterministic,
no LLM) from the expected_pages in the golden set.

Usage:
    python eval/run_eval.py --golden eval/golden_phonebook.jsonl
    python eval/run_eval.py --no-judge   # skip LLM metrics, retrieval-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

API_DEFAULT = "http://localhost:8000"


def load_golden(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def run_query(api: str, question: str) -> dict:
    body = {"question": question}
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


def score_retrieval_pages(sources, expected_pages):
    if not expected_pages:
        empty = len(sources) == 0
        return {"hit": 1.0 if empty else 0.0, "recall": 1.0 if empty else 0.0, "mrr": 1.0 if empty else 0.0}
    retrieved_pages = [s.get("page_number") for s in sources if s.get("page_number") is not None]
    hits = [p for p in retrieved_pages if p in expected_pages]
    hit = 1.0 if hits else 0.0
    recall = len(set(hits)) / len(expected_pages)
    mrr = 0.0
    for rank, p in enumerate(retrieved_pages, start=1):
        if p in expected_pages:
            mrr = 1.0 / rank
            break
    return {"hit": hit, "recall": recall, "mrr": mrr, "retrieved_pages": retrieved_pages}


_JUDGE_CLIENT = None
_CFG = None


def _judge_client():
    global _JUDGE_CLIENT, _CFG
    if _JUDGE_CLIENT is None:
        from openai import OpenAI
        _CFG = _load_env()
        _JUDGE_CLIENT = OpenAI(api_key=_CFG["CEREBRAS_API_KEY"], base_url=_CFG["CEREBRAS_BASE_URL"])
    return _JUDGE_CLIENT, _CFG


def _judge(prompt):
    client, c = _judge_client()
    try:
        resp = client.chat.completions.create(
            model=c["CEREBRAS_MODEL"],
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            temperature=0.0,
        )
        out = (resp.choices[0].message.content or "").strip()
        m = re.search(r"([01](?:\.\d+)?)\s*[|/]\s*(.+)", out)
        if m:
            return float(m.group(1)), m.group(2)
        m2 = re.search(r"\b(1(?:\.0)?|0(?:\.\d+)?)\b", out)
        if m2:
            return float(m2.group(1)), out[:140]
        return 0.5, f"unparseable: {out[:120]}"
    except Exception as e:
        return -1.0, f"judge error: {e}"


def _ctx_block(contexts):
    return "\n\n".join(f"[{i+1}] {c[:500]}" for i, c in enumerate(contexts[:6])) or "(no context retrieved)"


def context_precision(question, contexts):
    prompt = (
        "You are evaluating a RAG retrieval step. For EACH numbered chunk below, "
        "judge whether it is RELEVANT to answering the QUESTION (would help answer it).\n"
        "Then output the FRACTION of chunks that are relevant as a score 0.0 to 1.0.\n"
        "Format: SCORE|brief reason. Example: 0.67|2 of 3 chunks relevant.\n\n"
        f"QUESTION: {question}\n\nCHUNKS:\n{_ctx_block(contexts)}\n\nScore:"
    )
    return _judge(prompt)


def context_recall(question, reference_answer, contexts):
    prompt = (
        "You are evaluating a RAG retrieval step. Given the REFERENCE ANSWER and "
        "the retrieved CONTEXT, what fraction of the claims in the reference answer "
        "can be ATTRIBUTED to the context? (is the info there to support the answer?)\n"
        "Score 1.0 = fully attributable, 0.5 = partly, 0.0 = none of it.\n"
        "Format: SCORE|brief reason.\n\n"
        f"QUESTION: {question}\n\nREFERENCE ANSWER: {reference_answer}\n\n"
        f"CONTEXT:\n{_ctx_block(contexts)}\n\nScore:"
    )
    return _judge(prompt)


def faithfulness(question, answer, contexts):
    prompt = (
        "You are evaluating a RAG answer. Judge whether every claim in the ANSWER "
        "is SUPPORTED by the CONTEXT (no outside knowledge, no invention).\n"
        "Score 1.0 = fully supported, 0.5 = partly supported, 0.0 = mostly unsupported.\n"
        "Format: SCORE|brief reason.\n\n"
        f"QUESTION: {question}\n\nCONTEXT:\n{_ctx_block(contexts)}\n\nANSWER: {answer}\n\nJudge:"
    )
    return _judge(prompt)


def response_relevance(question, answer):
    prompt = (
        "You are evaluating a RAG answer. Does the ANSWER directly and completely "
        "address the QUESTION? (Not whether it's true - just whether it's relevant "
        "and complete as an answer to what was asked.)\n"
        "Score 1.0 = directly answers, 0.5 = partial/vague, 0.0 = doesn't address it.\n"
        "Format: SCORE|brief reason.\n\n"
        f"QUESTION: {question}\n\nANSWER: {answer}\n\nScore:"
    )
    return _judge(prompt)


def _load_env():
    env = Path(__file__).resolve().parents[2] / ".env"
    e = {}
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                e[k.strip()] = v.strip()
    return {
        "CEREBRAS_API_KEY": e.get("CEREBRAS_API_KEY", ""),
        "CEREBRAS_BASE_URL": e.get("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1"),
        "CEREBRAS_MODEL": e.get("CEREBRAS_MODEL", "gpt-oss-120b"),
    }


def is_decline(answer):
    low = answer.lower()
    return any(p in low for p in ["couldn't find", "don't contain", "not contain", "no documents are ready"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default=API_DEFAULT)
    ap.add_argument("--golden", default=str(Path(__file__).parent / "golden_phonebook.jsonl"))
    ap.add_argument("--no-judge", action="store_true")
    args = ap.parse_args()

    golden = load_golden(Path(args.golden))
    print(f"\n=== RAG eval: {len(golden)} questions vs {args.api} ===")
    print(f"=== Metrics: context_precision | context_recall | faithfulness | response_relevance ===\n")

    rows = []
    for i, item in enumerate(golden, 1):
        q = item["question"]
        expected = set(item.get("expected_pages", []))
        ref = item.get("reference_answer", "")
        print(f"[{i}/{len(golden)}] Q: {q}")

        result = run_query(args.api, q)
        answer = result["answer"]
        sources = result["sources"]
        contexts = [c.get("excerpt", "") for c in result["citations"]]
        declined = is_decline(answer)
        ret = score_retrieval_pages(sources, expected)
        is_off_topic = not expected

        cp, cp_r = (-1.0, "skipped")
        cr, cr_r = (-1.0, "skipped")
        faith, faith_r = (-1.0, "skipped")
        rel, rel_r = (-1.0, "skipped")

        if not args.no_judge:
            if is_off_topic:
                if declined:
                    cp = cr = faith = rel = 1.0
                    cp_r = cr_r = faith_r = rel_r = "correctly declined"
                else:
                    faith, faith_r = faithfulness(q, answer, contexts)
                    rel, rel_r = response_relevance(q, answer)
                    cp = cr = 0.0
                    cp_r = cr_r = "off-topic, should have declined"
            else:
                cp, cp_r = context_precision(q, contexts)
                if ref:
                    cr, cr_r = context_recall(q, ref, contexts)
                else:
                    cr, cr_r = (-1.0, "no reference_answer in golden")
                if declined:
                    faith, faith_r = (0.0, "declined on-topic question")
                    rel, rel_r = (0.0, "declined on-topic question")
                else:
                    faith, faith_r = faithfulness(q, answer, contexts)
                    rel, rel_r = response_relevance(q, answer)

        row = {
            "question": q,
            "off_topic": is_off_topic,
            "expected_pages": sorted(expected),
            "retrieved_pages": ret.get("retrieved_pages", []),
            "page_hit_rate": ret["hit"],
            "page_recall": ret["recall"],
            "page_mrr": ret["mrr"],
            "context_precision": round(cp, 2),
            "context_recall": round(cr, 2),
            "faithfulness": round(faith, 2),
            "response_relevance": round(rel, 2),
            "declined": declined,
            "cp_reason": cp_r[:120],
            "cr_reason": cr_r[:120],
            "faith_reason": faith_r[:120],
            "rel_reason": rel_r[:120],
            "answer_preview": answer[:200].replace("\n", " "),
        }
        rows.append(row)

        scored = [v for v in [cp, cr, faith, rel] if v >= 0]
        ok = (all(v >= 0.5 for v in scored) if scored and not args.no_judge else ret["hit"] > 0)
        status = "ok" if ok else "X "
        print(f"   {status} ctx_prec={cp:.2f} ctx_rec={cr:.2f} faith={faith:.2f} rel={rel:.2f} | "
              f"page_hit={ret['hit']:.0f} page_recall={ret['recall']:.2f}")
        if result["error"]:
            print(f"      error: {result['error']}")

    on_topic = [r for r in rows if not r["off_topic"]]
    off_topic = [r for r in rows if r["off_topic"]]

    def avg(xs, key):
        xs = [x for x in xs if x[key] >= 0]
        return sum(x[key] for x in xs) / len(xs) if xs else float("nan")

    print("\n" + "=" * 55)
    print("  RAG EVAL SUMMARY (4 core metrics)")
    print("=" * 55)
    print("\n  RETRIEVAL (did we fetch the right context?)")
    print(f"    context_precision : {avg(on_topic,'context_precision'):.2f}")
    print(f"    context_recall    : {avg(on_topic,'context_recall'):.2f}")
    print(f"    [page hit_rate    : {avg(on_topic,'page_hit_rate'):.2f}]")
    print(f"    [page recall@k    : {avg(on_topic,'page_recall'):.2f}]")
    print(f"    [page MRR         : {avg(on_topic,'page_mrr'):.2f}]")
    print("\n  GENERATION (did the LLM answer well?)")
    print(f"    faithfulness      : {avg(on_topic,'faithfulness'):.2f}")
    print(f"    response_relevance: {avg(on_topic,'response_relevance'):.2f}")
    if off_topic:
        declined_ok = sum(1 for r in off_topic if r["declined"])
        print(f"\n  OFF-TOPIC (should decline): {declined_ok}/{len(off_topic)} correctly declined")
    print("=" * 55)

    out = Path(__file__).parent / "results.json"
    out.write_text(json.dumps({"summary": {
        "context_precision": avg(on_topic, "context_precision"),
        "context_recall": avg(on_topic, "context_recall"),
        "faithfulness": avg(on_topic, "faithfulness"),
        "response_relevance": avg(on_topic, "response_relevance"),
        "page_hit_rate": avg(on_topic, "page_hit_rate"),
        "page_recall": avg(on_topic, "page_recall"),
        "page_mrr": avg(on_topic, "page_mrr"),
        "off_topic_declined": sum(1 for r in off_topic if r["declined"]) if off_topic else None,
        "n": len(rows),
    }, "rows": rows}, indent=2))
    print(f"\n  detailed results -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
