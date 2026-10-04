import json
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.agent.graph import build_graph
from app.agent.nodes import MODEL

HERE = Path(__file__).parent
QUESTIONS = HERE / "test_questions.json"
RESULTS = HERE / "results.json"

agent = build_graph()


def run_one(question: str):
    state = {"question": question, "profile": {}, "intent": "info", "topic": ""}
    for attempt in range(3):
        try:
            return agent.invoke(state)
        except Exception as e:
            print(f"   error (try {attempt + 1}/3): {str(e)[:100]}")
            time.sleep(20)
    return None


def main():
    tests = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    rows = []

    for t in tests:
        result = run_one(t["question"])
        expected = set(t["expected"])
        allowed = expected | set(t["acceptable"])

        if result is None:
            rows.append({**t, "error": True, "passed": False})
            print(f"[{t['id']:>2}] ERROR")
            continue

        sources = [s["name"] for s in result.get("sources", [])]
        retrieved = [d["name"] for d in result.get("docs", [])]

        if expected:  # in-scope sawal
            retrieval_hit = bool(expected & set(retrieved))
            answer_hit = bool(expected & set(sources))
            clean = set(sources) <= allowed
            passed = answer_hit and clean
        else:  # out-of-scope: koi source nahi aana chahiye
            retrieval_hit = None
            answer_hit = None
            clean = len(sources) == 0
            passed = clean

        rows.append(
            {
                **t,
                "sources": sources,
                "retrieved": retrieved,
                "retrieval_hit": retrieval_hit,
                "answer_hit": answer_hit,
                "clean": clean,
                "passed": passed,
                "answer": result.get("reply", ""),
            }
        )
        print(f"[{t['id']:>2}] {'PASS' if passed else 'FAIL'}  {t['question'][:55]}")
        if not passed:
            print(f"      expected : {t['expected']}")
            print(f"      got      : {sources}")
            print(f"      retrieved: {retrieved}")
        time.sleep(3)  # Groq free limit se bachne ke liye

    scoped = [r for r in rows if r["expected"] and not r.get("error")]
    oos = [r for r in rows if not r["expected"] and not r.get("error")]

    def pct(n, d):
        return f"{n}/{d} ({100 * n // d}%)" if d else "n/a"

    print("\n" + "=" * 52)
    print(f"Model                    : {MODEL}")
    print(f"Overall pass             : {pct(sum(r['passed'] for r in rows), len(rows))}")
    print(f"Retrieval recall@5       : {pct(sum(bool(r['retrieval_hit']) for r in scoped), len(scoped))}")
    print(f"Answer hit rate          : {pct(sum(bool(r['answer_hit']) for r in scoped), len(scoped))}")
    print(f"Clean (no wrong source)  : {pct(sum(r['clean'] for r in scoped), len(scoped))}")
    print(f"Out-of-scope refused     : {pct(sum(r['clean'] for r in oos), len(oos))}")
    print("=" * 52)

    RESULTS.write_text(
        json.dumps(
            {"time": datetime.now().isoformat(), "model": MODEL, "rows": rows},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Poora result evals/results.json mein save ho gaya.")


if __name__ == "__main__":
    main()