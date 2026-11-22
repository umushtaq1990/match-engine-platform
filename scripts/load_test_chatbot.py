"""Concurrent load test for the chatbot's one synchronous, Ollama-backed hot
path: `app.chatbot.graph.run_chat_turn`.

Companion to scripts/load_test.py and scripts/load_test_llm.py (same
methodology: plain ThreadPoolExecutor, client-side timing, no new
dependency), applied in-process rather than over HTTP -- the chatbot has no
REST endpoint of its own (it's a Streamlit page calling `run_chat_turn`
directly against a DB session and Ollama), so there's no URL to point
httpx at. Each worker thread opens its own `SessionLocal()` and its own
chat session via `create_session`, mirroring exactly what one real browser
tab does.

This exists specifically to put a number on the capacity guardrail
(`guardrails.MAX_CONCURRENT_CHAT_TURNS`, see docs/architecture/
application-services/rag-chatbot.md's "Capacity and availability
guardrails" section) under load heavier than the 7-thread smoke test that
shipped with it: does it decline cleanly past the limit, or does something
queue/crash instead? Responses are bucketed into ok / capacity_decline /
other_decline / error by inspecting the returned message against the
guardrails module's own decline-message constants, not by guessing from
text content.

Run with:
  python scripts/load_test_chatbot.py [--concurrency N] [--requests N] [--model MODEL]

Must run with the same Python environment/imports as the app itself (inside
the golden-drilldown container, or any shell with app.* importable and
DATABASE_URL/OLLAMA_BASE_URL pointed at the real services) -- this is not
an HTTP client, it imports and calls the app directly.

See docs/architecture/application-services/rag-chatbot.md for the results
and what this does/doesn't cover.
"""
import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.chatbot.chat_history import create_session
from app.chatbot.graph import run_chat_turn
from app.chatbot.guardrails import CAPACITY_DECLINE_MESSAGE, LLM_UNAVAILABLE_MESSAGE
from app.db import SessionLocal

QUESTIONS = [
    "What skills does candidate 1 have?",
    "What jobs are available for a backend engineer?",
    "What courses would help someone targeting job 1?",
    "Which candidates are most similar to candidate 2?",
    "What is the required experience for job 3?",
]


def one_turn(worker_id: int, model: str) -> tuple[float, str]:
    session = SessionLocal()
    t0 = time.perf_counter()
    try:
        chat_session = create_session(session, f"load-test-worker-{worker_id}", "admin")
        result = run_chat_turn(
            session, chat_session.id, "admin", QUESTIONS[worker_id % len(QUESTIONS)],
            model=model, enable_graph_tool=True, enable_rank_tool=True,
        )
        elapsed = time.perf_counter() - t0
        answer = result["answer"]
        if answer == CAPACITY_DECLINE_MESSAGE:
            outcome = "capacity_decline"
        elif answer == LLM_UNAVAILABLE_MESSAGE:
            outcome = "llm_unavailable"
        else:
            outcome = "ok"
        return elapsed, outcome
    except Exception as exc:  # noqa: BLE001 -- a crash IS a result category here, not a bug to hide
        elapsed = time.perf_counter() - t0
        return elapsed, f"error:{type(exc).__name__}"
    finally:
        session.close()


def _percentile(sorted_values: list[float], p: float) -> float:
    idx = min(int(len(sorted_values) * p), len(sorted_values) - 1)
    return sorted_values[idx]


def run(concurrency: int, requests: int, model: str) -> None:
    latencies: list[float] = []
    outcomes: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(one_turn, i, model) for i in range(requests)]
        t_start = time.perf_counter()
        for f in as_completed(futures):
            elapsed, outcome = f.result()
            latencies.append(elapsed)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        total_wall_time = time.perf_counter() - t_start

    sorted_latencies = sorted(latencies)
    n = len(latencies)
    print(f"model={model} concurrency={concurrency} requests={requests}")
    print(
        f"n={n} throughput={n / total_wall_time:.3f}req/s outcomes={outcomes} "
        f"p50={_percentile(sorted_latencies, 0.50):.2f}s "
        f"p95={_percentile(sorted_latencies, 0.95):.2f}s "
        f"p99={_percentile(sorted_latencies, 0.99):.2f}s "
        f"mean={statistics.mean(latencies):.2f}s max={sorted_latencies[-1]:.2f}s"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--model", default="qwen2.5:3b")
    args = parser.parse_args()
    run(args.concurrency, args.requests, args.model)


if __name__ == "__main__":
    main()
