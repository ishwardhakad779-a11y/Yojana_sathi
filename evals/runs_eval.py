import argparse
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
print_lock = threading.Lock()
stop_event = threading.Event()


class DailyLimit(Exception):
    """Groq ka daily token quota (TPD) khatam."""


def wait_time(msg: str, attempt: int) -> float:
    """Groq 429 error mein 'try again in 7.2s' likha hota hai, utna hi ruko."""
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", msg)
    if m:
        mins = int(m.group(1) or 0)
        val = float(m.group(2))
        secs = mins * 60 + (val / 1000 if m.group(3) == "ms" else val)
        return min(secs + 1, 120)
    return 10 * (attempt + 1)


def run_one(turns):
    """Ek sawal (str) ya multi-turn baatcheet (list of str) chalata hai."""
    if isinstance(turns, str):
        turns = [turns]

    state = {"question": "", "profile": {}, "intent": "info", "topic": ""}
    result = None

    for msg in turns:
        state = {**state, "question": msg}
        result = None

        for attempt in range(6):
            try:
                result = agent.invoke(state)
                break
            except Exception as e:
                err = str(e)
                if "tokens per day" in err or "(TPD)" in err:
                    stop_event.set()
                    raise DailyLimit(err)
                wait = wait_time(err, attempt)
                with print_lock:
                    print(f"   error (try {attempt + 1}/6, wait {wait:.0f}s): {err[:300]}")
                time.sleep(wait)

        if result is None:
            return None

        # agle turn ke liye profile/intent/topic aage bhejo
        state = {
            "question": msg,
            "profile": result.get("profile", {}),
            "intent": result.get("intent", "info"),
            "topic": result.get("topic", ""),
        }

    return result


def evaluate(t, pause):
    if stop_event.is_set():
        return None  # daily limit lag chuki hai, ye sawal skip

    result = run_one(t.get("turns") or t["question"])
    expected = set(t["expected"])
    allowed = expected | set(t["acceptable"])

    if result is None:
        return {**t, "error": True, "passed": False}

    sources = [s["name"] for s in result.get("sources", [])]
    retrieved = [d["name"] for d in result.get("docs", [])]
    reply = result.get("reply", "")

    if t.get("expect_ask"):  # adhoori profile: agent ko wapas poochna chahiye
        retrieval_hit = None
        answer_hit = None
        clean = len(sources) == 0 and "Bataiye" in reply
        passed = clean
    elif expected:  # in-scope sawal
        retrieval_hit = bool(expected & set(retrieved))
        answer_hit = bool(expected & set(sources))
        clean = set(sources) <= allowed
        passed = answer_hit and clean
    else:  # out-of-scope: koi source nahi aana chahiye
        retrieval_hit = None
        answer_hit = None
        clean = len(sources) == 0
        passed = clean

    time.sleep(pause)  # tokens-per-minute limit se bachne ke liye

    return {
        **t,
        "sources": sources,
        "retrieved": retrieved,
        "retrieval_hit": retrieval_hit,
        "answer_hit": answer_hit,
        "clean": clean,
        "passed": passed,
        "answer": reply,
    }


def select_tests(tests, ids, limit):
    if ids:
        wanted = set()
        for part in ids.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-")
                wanted.update(range(int(a), int(b) + 1))
            elif part:
                wanted.add(int(part))
        tests = [t for t in tests if t["id"] in wanted]
    if limit:
        tests = tests[:limit]
    return tests


def load_done():
    """Pichle run ke poore hue (bina error wale) results, agar model wahi hai."""
    if not RESULTS.exists():
        return []
    try:
        data = json.loads(RESULTS.read_text(encoding="utf-8"))
    except Exception:
        return []
    if data.get("model") != MODEL:
        print(f"Purane results ka model {data.get('model')} hai, abhi {MODEL}. Resume nahi kar raha, fresh shuru.")
        return []
    return [r for r in data.get("rows", []) if not r.get("error")]


def save(rows):
    rows = sorted(rows, key=lambda r: r["id"])
    RESULTS.write_text(
        json.dumps(
            {"time": datetime.now().isoformat(), "model": MODEL, "rows": rows},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=1, help="ek saath kitne sawal (safe: 1)")
    parser.add_argument("--pause", type=int, default=20, help="har sawal ke baad kitne second ruko")
    parser.add_argument("--resume", action="store_true", help="results.json ke poore hue sawal skip karo")
    parser.add_argument("--ids", type=str, default="", help="sirf ye ids, jaise 1,5,21-30")
    parser.add_argument("--limit", type=int, default=0, help="sirf pehle N sawal")
    args = parser.parse_args()

    all_tests = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    tests = select_tests(all_tests, args.ids, args.limit)

    rows = load_done() if args.resume else []
    done_ids = {r["id"] for r in rows}
    todo = [t for t in tests if t["id"] not in done_ids]

    if args.resume:
        print(f"Pehle se hue: {len(done_ids)}, baaki: {len(todo)}")

    start = time.time()
    finished = 0
    hit_daily_limit = False

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(evaluate, t, args.pause): t for t in todo}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                row = fut.result()
            except DailyLimit:
                hit_daily_limit = True
                continue
            except Exception as e:
                row = {**t, "error": True, "passed": False}
                with print_lock:
                    print(f"   crash in id {t['id']}: {str(e)[:100]}")

            if row is None:  # skip hua
                continue

            rows.append(row)
            finished += 1
            save(rows)  # har sawal ke baad checkpoint

            with print_lock:
                if row.get("error"):
                    print(f"[{t['id']:>2}] ERROR  ({finished}/{len(todo)})")
                else:
                    flag = "PASS" if row["passed"] else "FAIL"
                    print(f"[{t['id']:>2}] {flag}  ({finished}/{len(todo)})  {t['question'][:50]}")
                    if not row["passed"]:
                        print(f"      expected : {t['expected']}")
                        print(f"      got      : {row['sources']}")
                        print(f"      retrieved: {row['retrieved']}")
                        if t.get("expect_ask"):
                            print(f"      reply    : {row['answer'][:100]}")

    rows.sort(key=lambda r: r["id"])
    save(rows)

    ok = [r for r in rows if not r.get("error")]
    scoped = [r for r in ok if r["expected"]]
    asks = [r for r in ok if r.get("expect_ask")]
    oos = [r for r in ok if not r["expected"] and not r.get("expect_ask")]

    def pct(n, d):
        return f"{n}/{d} ({100 * n // d}%)" if d else "n/a"

    print("\n" + "=" * 52)
    print(f"Model                    : {MODEL}")
    print(f"Sawal complete           : {len(ok)}/{len(tests)}")
    print(f"Overall pass             : {pct(sum(r['passed'] for r in rows), len(rows))}")
    print(f"Retrieval recall@6       : {pct(sum(bool(r['retrieval_hit']) for r in scoped), len(scoped))}")
    print(f"Answer hit rate          : {pct(sum(bool(r['answer_hit']) for r in scoped), len(scoped))}")
    print(f"Clean (no wrong source)  : {pct(sum(r['clean'] for r in scoped), len(scoped))}")
    print(f"Asked for missing info   : {pct(sum(r['clean'] for r in asks), len(asks))}")
    print(f"Out-of-scope refused     : {pct(sum(r['clean'] for r in oos), len(oos))}")
    print(f"Errors                   : {len(rows) - len(ok)}")
    print(f"Time taken               : {time.time() - start:.0f} sec")
    print("=" * 52)

    if hit_daily_limit:
        print("\nDaily token limit (TPD) khatam. Ab tak ka kaam results.json mein save hai.")
        print("Baaki sawal ke liye limit wapas aane ke baad yahi command chalao:")
        print("   python -m evals.runs_eval --workers 1 --resume")
    else:
        print("Poora result evals/results.json mein save ho gaya.")


if __name__ == "__main__":
    main()