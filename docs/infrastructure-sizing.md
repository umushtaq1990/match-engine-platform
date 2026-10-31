# Infrastructure Sizing

Absorbs the earlier `hardware-inventory.md` measurement as its baseline input, plus a cost section
derived directly from the sizing below (no separate `cost-model.md` — cost without sizing is a
guess). Same discipline as everywhere else in this project: measured against the real running
stack, not estimated from first principles.

## Dev-machine baseline (measured)

| Item | Value |
|---|---|
| CPU | AMD Ryzen 7 250 (8 cores / 16 logical processors) |
| RAM | ~16 GB total; Docker Desktop's WSL2 VM allocates 16 CPUs / ~7.41 GiB to containers |
| GPU | NVIDIA RTX 5060 Laptop, 8151 MiB VRAM (unused — Ollama runs CPU inference; see `docs/model-serving.md`) |
| Disk | ~593 GB free |

Captured 2026-10-28 via PowerShell/WMI/`nvidia-smi` — see git history for the original
`hardware-inventory.md` if the raw capture is needed; this document is now the authoritative sizing
reference.

## Per-service memory footprint — measured, full stack running

`docker stats --no-stream` against every always-on service simultaneously (2026-10-31), ollama
idle (no model loaded mid-inference at capture time):

| Service | CPU % | Memory |
|---|---|---|
| `mlflow` | 1.79% | 2.17 GiB |
| `neo4j` | 1.07% | 1.05 GiB |
| `ollama` | 0.00% | 110 MiB (idle; **~2.56 GiB measured with two models loaded mid-inference**, see `docs/model-serving.md`) |
| `api` | 0.74% | 286 MiB |
| `analytics` | 0.70% | 63 MiB |
| `extraction` | 0.14% | 61 MiB |
| `db` | 0.00% | 59 MiB |
| `grafana` | 0.14% | 72 MiB |
| `prometheus` | 0.68% | 22 MiB |
| `cadvisor` | 0.33% | 28 MiB |
| `redis` | 0.47% | 6.5 MiB |
| **Total (idle, ollama unloaded)** | — | **~3.93 GiB** |
| **Total (ollama with 2 models loaded)** | — | **~6.4 GiB** |

**Reading this honestly**: ~6.4 GiB against a ~7.41 GiB WSL2 ceiling is comfortable but no longer
spacious — this is the real number that would force raising `.wslconfig`'s memory limit (assumption
A2) if one more memory-hungry service were added to this dev machine. `mlflow`'s 2.17 GiB at
near-idle is higher than its workload would suggest; not investigated further in this pass (a real
candidate for profiling if MLflow were ever memory-constrained in practice).

## Scaling scenarios

| Scenario | Candidates | Jobs | Measured outcome |
|---|---|---|---|
| Default (dev/CI) | 2,000 | 1,000 | Full pipeline (seed → embeddings → graph sync → clustering) runs in seconds-to-low-minutes; this is what every test suite and CI run exercises |
| Target scale (Phase 20 stress test) | 1,000,000 | 50,000 | Postgres bulk-insert generation: **11m40s** for ~7.4M total rows. Neo4j graph sync: **15m50s** for ~1.06M nodes / ~6.4M edges. Semantic matching endpoint (after fixing a real N+1): **1.2s** at full 50K-job scale, vs. rule-based's **10.7s** — the concrete case for Phase 17's two-stage retrieve-then-score architecture |

Full methodology and every number: `docs/data-at-scale.md`.

## Kubernetes resource allocation (local `kind`, verified; Azure overlay documented, not deployed)

Only `api`'s Deployment has explicit CPU/memory requests and limits set — the one component load-
tested enough to size with any confidence (`docs/production-readiness.md`). Other components rely
on cluster/namespace defaults, a real, documented gap, not an oversight:

| Resource | Requests | Limits |
|---|---|---|
| `api` (per pod, ×2 replicas, HPA 2–5) | 100m CPU / 128Mi memory | 500m CPU / 512Mi memory |
| `postgres`, `grafana`, `prometheus`, `mlflow` | Only PVC storage requests set (1–2Gi each) | Not set |

**What sizing `api`'s limits was based on**: the Phase 13 load test measured throughput scaling
from 14.2 → 56.1 req/s moving from 1 to 4 uvicorn workers on this dev machine's 8 physical cores;
500m CPU per pod with 2–5 replicas via HPA approximates spreading that same worker-level
parallelism across pods instead of within one, a reasonable starting point, not a number derived
from a Kubernetes-specific load test (none was run against the `kind` cluster — see
`docs/kubernetes.md` "Known gaps").

## Cost — illustrative, derived from the sizing above, not a quote

No Azure subscription was ever provisioned for this project (assumption A1) — the numbers below are
back-of-envelope, built directly from the measured sizing above and Azure's published (not
negotiated) list pricing as of this writing, to make "what would this cost on Azure" a documented,
falsifiable estimate rather than silence:

| Component | Azure equivalent | Illustrative monthly cost (list price, 1 instance/replica set) |
|---|---|---|
| `db` (PostgreSQL) | Azure Database for PostgreSQL Flexible Server, Burstable B2s (2 vCPU/4GiB) | ~$55 |
| `api` (2–5 pods, 500m/512Mi each) | AKS node pool share (amortized, not a dedicated VM) | ~$30–75 at this pod sizing |
| `redis` | Azure Cache for Redis, Basic C0 (250MB) | ~$17 |
| `neo4j` | Self-managed on an AKS node (no Azure-native Neo4j PaaS) | Amortized into AKS node cost |
| `ollama` | Self-managed on an AKS node (CPU inference; no GPU node pool provisioned) | Amortized into AKS node cost |
| AKS control plane (Free tier) | — | $0 |
| ACR (Basic) | — | ~$5 |
| **Illustrative total** | | **~$110–150/month** for a small, single-environment deployment |

This is explicitly a sizing-derived estimate for a *small* single-environment deployment matching
this project's own measured footprint — not a production capacity plan (no HA, no multi-region, no
reserved-instance discounting). A real cost exercise would start here and layer in actual expected
request volume, redundancy requirements, and committed-use pricing.

## What would change this sizing

- Running `embeddings-compute`/`extraction-eval` against the full 1M/50K population (currently
  sampled by design, `docs/data-at-scale.md`) would meaningfully increase Ollama CPU time and
  wall-clock duration — not yet measured, since the current approach deliberately doesn't need it.
- Real production traffic volume (unmeasured — this project has no real users) would be the actual
  driver of `api`'s replica count and `db`'s instance size, not this synthetic dataset's load test.
