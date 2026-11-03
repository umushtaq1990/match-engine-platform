"""Concurrent load test for the LLM/embedding (Ollama-backed) extraction path.

Companion to scripts/load_test.py (same methodology: plain httpx +
ThreadPoolExecutor, client-side timing, no new dependency), applied to the
one live endpoint that actually exercises Ollama synchronously: POST
/extract on the extraction service (app/extraction/routes.py). There is no
live embeddings endpoint to test separately — app/embeddings/ is batch-job
only (embeddings-compute) — so this is the one concurrency-testable
LLM/embedding path in the whole platform today, a finding in itself.

Unlike the matching API, /extract's latency is not fixed per request: each
call makes one LLM generate() call plus one embed() call per extracted skill
phrase (app/extraction/pipeline.py), all sequential. This script logs the
returned skill count per request alongside latency so the two effects
(queuing under concurrency vs. per-request extraction-count variance) can be
told apart rather than conflated into one number.

Run with:
  python scripts/load_test_llm.py [--concurrency N] [--requests N] [--direct]

--direct hits extraction:8002 directly (bypassing the Gateway) to confirm
nginx isn't adding meaningful overhead of its own; default goes through the
real Gateway path (http://localhost:8099/extraction/extract), the same path
the Frontend itself uses.

See docs/operations/capacity-planning.md for the results and what this
does/doesn't cover.
"""
import argparse
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

API_KEY = os.environ.get("JME_API_KEY", "dev-local-key-change-me")
GATEWAY_URL = os.environ.get("JME_GATEWAY_URL", "http://localhost:8099/extraction")
DIRECT_URL = os.environ.get("JME_EXTRACTION_URL", "http://localhost:8002")

# A handful of representative job/CV description texts of varying length and
# skill density, so requests don't all take an identical path through the
# pipeline.
SAMPLE_TEXTS = [
    "We are looking for a backend engineer experienced in Python, FastAPI, "
    "PostgreSQL, and Docker. Experience with Kubernetes and CI/CD pipelines "
    "is a strong plus.",
    "The candidate has five years of experience in data analysis, using SQL, "
    "Excel, and Tableau to build dashboards for executive stakeholders.",
    "Seeking a project manager with strong communication skills, experience "
    "running Agile/Scrum ceremonies, and a background in stakeholder "
    "management across cross-functional engineering and design teams.",
    "Registered nurse with experience in patient care, electronic health "
    "records (Epic), and medication administration in a hospital setting.",
]


def one_request(client: httpx.Client, base_url: str, text: str) -> tuple[float, int, int]:
    t0 = time.perf_counter()
    try:
        resp = client.post(
            f"{base_url}/extract",
            json={"text": text},
            headers={"X-API-Key": API_KEY},
        )
        elapsed = time.perf_counter() - t0
        # ExtractResponse = {"matches": [...], "unmatched": [...]} (app/extraction/schemas.py)
        n_skills = len(resp.json().get("matches", [])) if resp.status_code == 200 else -1
        return elapsed, resp.status_code, n_skills
    except httpx.HTTPError as exc:
        elapsed = time.perf_counter() - t0
        print(f"  request failed after {elapsed:.1f}s: {exc!r}")
        return elapsed, -1, -1


def _percentile(sorted_values: list[float], p: float) -> float:
    idx = min(int(len(sorted_values) * p), len(sorted_values) - 1)
    return sorted_values[idx]


def run(base_url: str, concurrency: int, requests: int) -> None:
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    skill_counts: list[int] = []

    with httpx.Client(timeout=90.0) as client:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(one_request, client, base_url, SAMPLE_TEXTS[i % len(SAMPLE_TEXTS)])
                for i in range(requests)
            ]
            t_start = time.perf_counter()
            for f in as_completed(futures):
                elapsed, status, n_skills = f.result()
                latencies.append(elapsed)
                statuses[status] = statuses.get(status, 0) + 1
                if n_skills >= 0:
                    skill_counts.append(n_skills)
            total_wall_time = time.perf_counter() - t_start

    sorted_latencies = sorted(latencies)
    n = len(latencies)
    print(f"base_url={base_url} concurrency={concurrency} requests={requests}")
    print(
        f"n={n} throughput={n / total_wall_time:.2f}req/s statuses={statuses} "
        f"p50={_percentile(sorted_latencies, 0.50):.2f}s "
        f"p95={_percentile(sorted_latencies, 0.95):.2f}s "
        f"p99={_percentile(sorted_latencies, 0.99):.2f}s "
        f"mean={statistics.mean(latencies):.2f}s max={sorted_latencies[-1]:.2f}s"
    )
    if skill_counts:
        print(
            f"skills_extracted: mean={statistics.mean(skill_counts):.1f} "
            f"min={min(skill_counts)} max={max(skill_counts)}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument(
        "--direct", action="store_true",
        help="Hit extraction:8002 directly instead of through the Gateway.",
    )
    args = parser.parse_args()
    base_url = DIRECT_URL if args.direct else GATEWAY_URL
    run(base_url, args.concurrency, args.requests)


if __name__ == "__main__":
    main()
