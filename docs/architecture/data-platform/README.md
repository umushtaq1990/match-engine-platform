# Data Platform

Level-3 diagram (cropped from the master Technical Architecture diagram):[`data-platform.drawio`](data-platform.drawio).

## Purpose & Scope

Every data store this platform runs, and the two cross-cutting concerns that sit on top of them (embeddings for semantic matching, and automated data quality checking). Each store was added for one scoped, measured reason, not as a default "use this database" choice, see [architecture.md](../../architecture.md) section 3 ("Redis only for three scoped, measured use cases", "Two-stage retrieve-then-score for anything at scale"). Each is documented in full as its own Level-4 doc below; this page is only the index and the reasoning for why each store exists.

## Why each store exists

- **PostgreSQL 16 + pgvector** is the system of record for every relational entity (candidates, jobs, skills, courses) and, via pgvector, the ANN index that makes semantic matching tractable at scale (a two-stage retrieve-then-score pattern: narrow thousands-to-millions of rows to a few hundred plausible ones before any expensive scoring runs).
- **Neo4j 5 + GDS** exists because some questions (skill clustering, candidate/job similarity by shared-skill patterns) are graph-shaped, not relational-shaped, native Louvain clustering in Cypher would be awkward or slow to replicate in SQL.
- **Redis 7** was added only once three separate, measured needs existed: a true global rate limit across multiple worker processes (fixing a real bug), a result cache with active invalidation, and Streams-based decoupling of the analytics instrumentation hook.
- **SeaweedFS** is S3-compatible object storage for the one kind of unstructured data this platform handles (uploaded CVs), kept separate from Postgres so a 2MB, PDF upload isn't a relational row.

## Components

| Store / concern | Purpose | Level-4 doc |
|---|---|---|
| PostgreSQL 16 + pgvector | Relational schema, embeddings storage, ANN retrieval | [data-model.md](data-model.md), [embedding-matching.md](embedding-matching.md) |
| Neo4j 5 + GDS | Knowledge graph: skill clustering, candidate/job clustering | [knowledge-graph.md](knowledge-graph.md) |
| Redis 7 | Rate limiting, result caching, Streams (analytics decoupling) | [redis-infrastructure.md](redis-infrastructure.md) |
| SeaweedFS | S3-compatible CV upload storage | [skill-extraction.md](../application-services/skill-extraction.md) |
| Data quality | Automated post-seed checks (orphaned FKs, duplicates, missing fields) | [data-quality.md](data-quality.md) |
| Data at scale | 1M-candidate/50K-job generation and stress testing | [data-at-scale.md](data-at-scale.md) |

## Resource Footprint and Scaling

Every store here is a single instance with no HA/replica story, a deliberate documented local scope (see `docs/assumptions-register.md`), not an oversight. Postgres is the one with the clearest numeric ceiling today: default `max_connections=100` (confirmed on the live container, not assumed from the image's documented default), against up to ~90 possible connections from `api`/`analytics`/`extraction` alone at their current worker counts (SQLAlchemy's default `pool_size=5, max_overflow=10` per worker process, `app/db.py`, no override configured). Real headroom exists, but a fourth heavy Postgres consumer or a jump in `api`'s worker count would need either a connection pooler (PgBouncer) or raising `max_connections`, neither built here. Redis has no measured connection ceiling issue at this scale (rate limiting, caching, and Streams all share one lightweight instance, see `redis-infrastructure.md`). Neo4j and SeaweedFS are read-heavy batch/upload paths, not in the hot request path for concurrent matching traffic, so they're not where this platform's real concurrency limits sit. Full worked scenario: `docs/operations/capacity-planning.md`.

## Related Documents

- [../ai-ml-and-mlops.md](../ai-ml-and-mlops.md) : the model-serving layer that produces the embeddings stored here
- [../application-services/README.md](../application-services/README.md) : the services that read and write this data
- [../../architecture.md](../../architecture.md) : overall system architecture
