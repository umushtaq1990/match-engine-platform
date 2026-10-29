"""Lightweight concurrent load test for the matching API (Phase 13 DoD item:
"at least one load test has been run and bottlenecks identified").

Not a replacement for a real tool (k6/Locust/wrk) for anything beyond this
project's scope - written in-repo instead of adding a new dependency for one
measurement, and so the methodology is fully visible rather than hidden
behind a tool's defaults. See docs/production-readiness.md for the results
and what this does/doesn't cover.

Run with: python scripts/load_test.py [--concurrency N] [--requests N]
"""
import argparse
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=500)
    args = parser.parse_args()

    latencies: list[float] = []
    statuses: dict[int, int] = {}

    with httpx.Client(timeout=30.0) as client:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [
                pool.submit(one_request, client, (i % 2000) + 1)
                for i in range(args.requests)
            ]
            t_start = time.perf_counter()
            for f in as_completed(futures):
                elapsed, status = f.result()
                latencies.append(elapsed)
                statuses[status] = statuses.get(status, 0) + 1
            total_wall_time = time.perf_counter() - t_start

    latencies.sort()

    def pct(p: float) -> float:
        idx = min(int(len(latencies) * p), len(latencies) - 1)
        return latencies[idx]

    print(f"Requests: {args.requests}, concurrency: {args.concurrency}")
    print(f"Wall time: {total_wall_time:.2f}s, throughput: {args.requests / total_wall_time:.1f} req/s")
    print(f"Status codes: {statuses}")
    print(f"Latency (s) — min: {latencies[0]:.4f}, mean: {statistics.mean(latencies):.4f}, "
          f"p50: {pct(0.50):.4f}, p95: {pct(0.95):.4f}, p99: {pct(0.99):.4f}, max: {latencies[-1]:.4f}")


if __name__ == "__main__":
    main()
