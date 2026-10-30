"""Concurrent load/soak test for the matching API.

Fixed-count mode (Phase 13 DoD item: "at least one load test has been run
and bottlenecks identified") runs a fixed number of requests and reports one
aggregate summary — good for finding a throughput/latency bottleneck at a
point in time, which is what found the single-worker-process issue in
Phase 13. It cannot show degradation *over time* (a slow leak, a connection
pool exhausting, latency creeping up under sustained load) because it never
runs long enough to show a trend.

Soak mode (--soak-minutes, Phase 18 DoD item: "a sustained load/soak test
and a real capacity plan, not one load test at one point in time") runs
continuously at a fixed concurrency and reports one summary per time window,
so a trend across windows — not just a single snapshot — is visible.

Not a replacement for a real tool (k6/Locust/wrk) for anything beyond this
project's scope - written in-repo instead of adding a new dependency for one
measurement, and so the methodology is fully visible rather than hidden
behind a tool's defaults. See docs/production-readiness.md for the results
and what this does/doesn't cover.

Run with:
  python scripts/load_test.py [--concurrency N] [--requests N]
  python scripts/load_test.py --soak-minutes 10 [--concurrency N] [--window-seconds N]
"""
import argparse
import os
import statistics
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait

import httpx

API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
BASE_URL = os.environ.get("JME_API_URL", "http://localhost:8000")


def one_request(client: httpx.Client, candidate_id: int) -> tuple[float, int]:
    t0 = time.perf_counter()
    resp = client.get(
        f"{BASE_URL}/candidates/{candidate_id}/job-matches",
        params={"top_k": 10},
        headers={"X-API-Key": API_KEY},
    )
    elapsed = time.perf_counter() - t0
    return elapsed, resp.status_code


def _percentile(sorted_values: list[float], p: float) -> float:
    idx = min(int(len(sorted_values) * p), len(sorted_values) - 1)
    return sorted_values[idx]


def _summarize(latencies: list[float], statuses: dict[int, int], wall_time: float) -> str:
    sorted_latencies = sorted(latencies)
    n = len(latencies)
    return (
        f"n={n} throughput={n / wall_time:.1f}req/s statuses={statuses} "
        f"p50={_percentile(sorted_latencies, 0.50):.4f}s "
        f"p95={_percentile(sorted_latencies, 0.95):.4f}s "
        f"p99={_percentile(sorted_latencies, 0.99):.4f}s "
        f"mean={statistics.mean(latencies):.4f}s max={sorted_latencies[-1]:.4f}s"
    )


def run_fixed_count(concurrency: int, requests: int) -> None:
    latencies: list[float] = []
    statuses: dict[int, int] = {}

    with httpx.Client(timeout=30.0) as client:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(one_request, client, (i % 2000) + 1) for i in range(requests)
            ]
            t_start = time.perf_counter()
            for f in as_completed(futures):
                elapsed, status = f.result()
                latencies.append(elapsed)
                statuses[status] = statuses.get(status, 0) + 1
            total_wall_time = time.perf_counter() - t_start

    print(f"Requests: {requests}, concurrency: {concurrency}")
    print(_summarize(latencies, statuses, total_wall_time))


def run_soak(concurrency: int, duration_minutes: float, window_seconds: int) -> list[str]:
    end_time = time.perf_counter() + duration_minutes * 60
    window_start = time.perf_counter()
    window_latencies: list[float] = []
    window_statuses: dict[int, int] = {}
    window_summaries: list[str] = []
    request_counter = 0

    print(f"Soak test: concurrency={concurrency}, duration={duration_minutes}min, "
          f"window={window_seconds}s")

    with httpx.Client(timeout=30.0) as client:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            in_flight = set()
            while time.perf_counter() < end_time or in_flight:
                while len(in_flight) < concurrency and time.perf_counter() < end_time:
                    in_flight.add(
                        pool.submit(one_request, client, (request_counter % 2000) + 1)
                    )
                    request_counter += 1
                done, in_flight = wait(in_flight, timeout=0.5, return_when=FIRST_COMPLETED)
                for f in done:
                    elapsed, status = f.result()
                    window_latencies.append(elapsed)
                    window_statuses[status] = window_statuses.get(status, 0) + 1

                if time.perf_counter() - window_start >= window_seconds and window_latencies:
                    summary = f"[window {len(window_summaries) + 1}] " + _summarize(
                        window_latencies, window_statuses, time.perf_counter() - window_start
                    )
                    print(summary)
                    window_summaries.append(summary)
                    window_latencies, window_statuses = [], {}
                    window_start = time.perf_counter()

    return window_summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument(
        "--soak-minutes", type=float, default=None,
        help="If set, run continuously for this many minutes instead of --requests, "
        "reporting one summary per --window-seconds window.",
    )
    parser.add_argument("--window-seconds", type=int, default=60)
    args = parser.parse_args()

    if args.soak_minutes is not None:
        run_soak(args.concurrency, args.soak_minutes, args.window_seconds)
    else:
        run_fixed_count(args.concurrency, args.requests)


if __name__ == "__main__":
    main()
