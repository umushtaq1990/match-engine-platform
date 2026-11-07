# Application Services

Level-3 diagram (cropped from the master Technical Architecture diagram): [`application-services.drawio`](application-services.drawio).

## Purpose & Scope

The three services that carry this platform's actual business logic. Each is its own independently deployable Docker image with its own `requirements` file, not one monolith split by folder, following the "service boundaries follow bounded contexts, not convenience" principle laid out in [architecture.md](../../architecture.md) section 3. Each is documented in full as its own Level-4 doc below; this page is only the index and the reasoning for why they're split the way they are.

## Why three separate services

- **`api`** (matching engine) owns candidate/job matching, skill-gap analysis, and course recommendations, the core product logic, the heaviest read traffic, and the only service that needed 4 uvicorn workers to meet its measured latency target (see [operations/production-readiness.md](../../operations/production-readiness.md)).
- **`analytics`** owns behavioral event ingestion and segmentation. It is deliberately decoupled from `api`'s request path via Redis Streams, a real match response never blocks on analytics being up (a fail-open design, same discipline used for the embeddings cache). Splitting it out means its own load (event volume) never competes with `api`'s own scaling needs.
- **`extraction`** owns free-text skill parsing from CV/job text, including the LLM/embedding calls that `api` never makes directly. Keeping extraction separate means `api`'s own image doesn't need the extraction pipeline's dependencies, and extraction can be scaled or redeployed independently of matching.

## Services

| Service | Purpose | Level-4 doc |
|---|---|---|
| `api` | Candidate/job matching (rule-based + embeddings-blended), skill-gap analysis, course recommendations | [matching-engine.md](matching-engine.md) |
| `analytics` | Behavioral event ingestion, Redis Streams consumer, RFM engagement segmentation | [behavioral-analytics.md](behavioral-analytics.md) |
| `extraction` | Free-text skill extraction (hybrid LLM + embedding canonicalization), CV upload storage | [skill-extraction.md](skill-extraction.md) |

## Resource Footprint and Scaling

The three services have very different concurrency models today, confirmed from their actual Dockerfiles, not assumed:

| Service | Workers | Real measured/known ceiling | Scaling path |
|---|---|---|---|
| `api` | 4 uvicorn workers | 56.1 req/s at concurrency 20 (was 14.2 req/s at 1 worker), [matching-engine.md](matching-engine.md) | Already horizontally scalable: k8s HPA defined (2-5 pods, `k8s/base/api.yaml`), though inert locally without `metrics-server` |
| `analytics` | 1 uvicorn worker | Not load-tested directly; decoupled from `api`'s request path via Redis Streams, so it doesn't block matching responses under load [behavioral-analytics.md](behavioral-analytics.md) | No k8s manifest yet; same `--workers N` fix `api` already got would be the first step |
| `extraction` | 1 uvicorn worker, synchronous route | See [skill-extraction.md](skill-extraction.md) for the real measured LLM-backed concurrency numbers | No k8s manifest yet; needs both more workers and the AI/ML layer's own scaling (see [../ai-ml-and-mlops.md](../ai-ml-and-mlops.md)) |

All three share one Postgres instance with SQLAlchemy's default pool (`pool_size=5,max_overflow=10` per worker process, `app/db.py`, no override configured anywhere). At today's worker counts that's up to 90 possible connections from these three services alone against Postgres's own default `max_connections=100` (confirmed on the live container) real headroom, but not a lot if a fourth heavy consumer were added without raising either number. Full worked scenario: [../../operations/capacity-planning.md](../../operations/capacity-planning.md).

## Related Documents

- [../ai-ml-and-mlops.md](../ai-ml-and-mlops.md) : the shared model-serving layer `extraction` and the embeddings pipeline call
- [../data-platform/README.md](../data-platform/README.md) : the data stores all three services read and write
- [../ingress-and-identity.md](../ingress-and-identity.md) : how requests reach these services
- [../../architecture.md](../../architecture.md) : overall system architecture
