# Behavioral Analytics & User Engagement Tracking (Phase 14)

Status: v1 — **implemented and verified end-to-end** (ingestion API, storage, instrumentation
hook, synthetic event generator). Segmentation/clustering (v2/v3 below) remain design-only. This
document was written and reviewed before any code existed, the same discipline Phase 12
(Kubernetes) and Phase 10 (offline bundle) were planned under — get the shape right on paper
first, then build against it. The design below matched the build with no changes required.

## Why this is a separate service, not a feature of the matching engine

The matching engine is low-volume and read-heavy: a case worker asks for ranked matches a handful
of times a session. Behavioral tracking is the opposite shape — high-volume, append-only,
fire-and-forget writes (every impression, every click) that must never slow down or block a
request that's actually serving a user. Those two workloads have different scaling, ownership, and
failure-tolerance profiles, and in a real organization they're usually owned by different teams
(platform/matching vs. growth/analytics).

So this is modeled as its own bounded context from day one — its own image, own schema, own API —
the same separation-of-concerns principle Phase 2 already applied to `ingestion`/`migrate`/`api`
(see `docs/adr/0001-decouple-ingestion-migrate-api.md`), just drawn one level higher, between
*products* rather than between steps of one pipeline.

## Scope (v1)

Track three kinds of actor behavior against the existing candidate/job/course domain, without
building anything downstream yet:
- **Impressions**: a recommendation was shown (candidate saw ranked jobs, employer saw ranked
  candidates, candidate saw course recommendations).
- **Interactions**: the actor clicked/opened/saved/applied in response.
- **Session context**: enough to later reconstruct "shown → clicked → applied" funnels per actor,
  per session.

Explicitly **not** in v1: no segmentation, no clustering, no dashboard, no streaming ingestion. All
listed under "Phased roadmap" below as what this unlocks, not what it includes.

## Service boundary

| | Decision | Rationale |
|---|---|---|
| Service | New `analytics` FastAPI service, own `docker/Dockerfile.analytics`, own `app/analytics/` package | Independently deployable/scalable; no coupling to `app/matching` or `app/api` code |
| Storage | Same Postgres **instance**, separate **schema** (`analytics`), for v1 | Cheapest way to get real bounded-context separation (own migrations, own tables, no cross-schema FK) without standing up a second database container before there's a reason to. A dedicated event store (or ClickHouse-style columnar store for analytical queries at scale) is a documented future step, not built now — same "prove the simple thing first" rule as everything else in this project |
| Cross-service reference | `actor_id`/`entity_id` are **plain integers, no FK constraint** to `candidates`/`jobs` | A genuine architectural choice, not an oversight: a hard FK would couple the analytics schema's migrations to the matching engine's table lifecycle. Referential integrity is checked by an analytics-side data-quality check instead (reusing the Phase 2 data-quality pattern — see below), which can *report* an orphan reference without *blocking* on one |
| Auth | Same `X-API-Key` pattern as the matching API (Phase 6) | Consistent with the existing security model; no new auth mechanism invented for one more service |

## Event schema (v1)

One generic, append-only table — not one table per event type. A fixed taxonomy lives in
application code (a Python enum/list), not a Postgres `ENUM` type, so adding a new event type is a
one-line code change, not a migration.

```
behavioral_events
  event_id      UUID        primary key, server-generated
  occurred_at   TIMESTAMPTZ  client-supplied; nullable (event may arrive without it)
  received_at   TIMESTAMPTZ  NOT NULL, server default now() — authoritative for ordering
  actor_type    TEXT         'candidate' | 'employer'
  actor_id      INTEGER      NOT NULL — see "no FK" above
  session_id    TEXT         nullable (anonymous/unauthenticated events have none)
  event_type    TEXT         NOT NULL — see taxonomy below
  entity_type   TEXT         nullable — 'job' | 'candidate' | 'course'
  entity_id     INTEGER      nullable
  source        TEXT         NOT NULL default 'api' — future-proofs for 'web'/'mobile' channels
  metadata      JSONB        NOT NULL default '{}'
```

Indexes: `(actor_type, actor_id, received_at)` for "this actor's timeline" queries,
`(event_type, received_at)` for "all events of this type in a window" queries. A GIN index on
`metadata` is deferred until a query actually needs to filter inside it.

**v1 event taxonomy**:

| event_type | entity_type | metadata (example) | Emitted by |
|---|---|---|---|
| `recommendation_shown` | job / candidate / course | `{"rank": 3, "score": 0.71, "algorithm_version": "rule-based-v1"}` | Instrumentation hook in the existing matching API (automatic, not client-initiated) |
| `recommendation_clicked` | job / candidate / course | `{"rank": 3}` | Client calls `POST /events` |
| `job_applied` | job | `{}` | Client calls `POST /events` |
| `job_saved` | job | `{}` | Client calls `POST /events` |
| `search_performed` | — | `{"query": "...", "result_count": 12}` | Client calls `POST /events` |
| `login` | — | `{}` | Client calls `POST /events` |

`algorithm_version` on `recommendation_shown` is included deliberately: it's what will later let a
future A/B comparison ("did the embedding-based matcher get clicked more than the rule-based one?")
be answered from this table, without redesigning it.

## Why `recommendation_shown` must be automatic, not client-reported

Clicks and applies are naturally reported by whatever calls the API. Impressions are not — nothing
client-side has a reason to tell you "I was shown item #3 at rank 3 with score 0.71" unless you
make it. Without a logged impression, a later click has no denominator, and click-through-rate
(clicks ÷ impressions) — one of the most basic and valuable metrics this whole subsystem exists to
produce — becomes uncomputable retroactively.

So the instrumentation point is in `app/api/routes.py`, not in a client SDK: every response from
`GET /candidates/{id}/job-matches`, `GET /jobs/{id}/candidate-matches`, and
`GET /candidates/{id}/course-recommendations` also fires one `recommendation_shown` event per
returned item, via `POST /events/batch` (a batch endpoint specifically so N returned items cost one
HTTP call to the analytics service, not N).

**v1 is synchronous and best-effort**: the matching API calls the analytics API in-line, logs and
swallows a failure (never lets an analytics outage break a real matching response), and does not
retry. This is a deliberate, documented limitation — see "Known limitations" below — not an
oversight; making it properly non-blocking (background task, or a queue) is listed under the
phased roadmap.

## API contract (v1)

| Endpoint | Purpose |
|---|---|
| `POST /events` | Ingest one client-reported event |
| `POST /events/batch` | Ingest many events in one call (used by the instrumentation hook, and available to any future client) |
| `GET /actors/{actor_type}/{actor_id}/events?event_type=&limit=` | Raw, paginated timeline for one actor — debugging/demo use, not a product endpoint |

All three require `X-API-Key`, same as the existing matching API.

## Synthetic event generator

Mirrors `app/ingestion/generate_synthetic_data.py`'s approach: deterministic (fixed seed),
referencing the *already-seeded* candidate/job/course ids, with intentional imperfections so the
data has real signal rather than being trivially clean:
- The large majority of `recommendation_shown` events get no follow-up interaction at all (realistic
  — most impressions are ignored).
- A smaller fraction get `recommendation_clicked`; a smaller fraction still of those get
  `job_applied` — modeling a realistic drop-off funnel, not a 1:1:1 pipeline.
- A fraction of events carry no `session_id`, modeling anonymous/not-logged-in traffic.
- A handful of duplicate events (same actor+entity+type within the same second) are injected
  deliberately, so `dedup` is something the design has to actually handle, not assume away.

## Phased roadmap

- **v1 (Phase 14)** — this document's scope: ingestion API, storage, instrumentation hook,
  synthetic event generator, a data-quality check reusing the Phase 2 pattern (orphan `actor_id`/
  `entity_id` references reported, not blocked — see "no FK" above).
- **v2 — rule-based segmentation**: per-actor RFM-style features (recency of last event, frequency
  of events in a rolling window, conversion rate = applies ÷ impressions) rolled up into labels
  via documented thresholds — `active_seeker` / `passive_seeker` / `dormant` for candidates,
  equivalent labels for employers. Exposed via a new summary endpoint. Deliberately simple and
  deliberately first, mirroring assumption A5's "rule-based baseline before ML" principle that
  already governs the matching engine itself.
- **v3 — ML clustering, only if v2 is shown insufficient**: real clustering (k-means/HDBSCAN) over
  engagement feature vectors, evaluated against the v2 rule-based labels the same way
  `docs/evaluation-strategy.md` evaluates matching quality — a measured comparison, not an assumed
  improvement.
- **v4 — scale-driven hardening**: streaming ingestion (Kafka/Redis Streams) if/when synchronous
  HTTP ingestion is shown to be a bottleneck, a non-blocking instrumentation hook (background task
  or queue instead of an inline call), a Grafana panel reusing the Phase 9 observability stack, and
  an analytics-specific data-quality `CronJob` mirroring the Phase 9/12 pattern.

None of v2–v4 are scheduled yet — recorded here so the shape of "simple now, advanced later" is
explicit rather than improvised when the time comes.

## Privacy/consent (new assumption)

All actor data here is synthetic, so there is no real privacy exposure in this project. Recorded as
assumption **A10** in `docs/assumptions-register.md`: a real deployment of this subsystem would
need an explicit lawful basis/consent mechanism, pseudonymization or anonymization options, and a
data-retention/deletion policy before tracking real user behavior — none of that is built here, and
it should be treated as a precondition for any real use, not an afterthought bolted on later.

## Verified against real data (not fabricated)

- **Migration**: `alembic revision --autogenerate` correctly detected the new schema-qualified
  table from the ORM model (`CREATE SCHEMA` itself still had to be added by hand — Alembic never
  generates that). Applied with `alembic upgrade head` against the real dev Postgres; `alembic
  check` confirmed zero drift between the ORM model (including both indexes) and the live schema.
- **Synthetic generator**: `python -m app.analytics.generate_synthetic_events` produced 28,218
  events against the real seeded candidate/job/course ids. Verified in Postgres, not just by the
  script's own print statement: 20,642 `recommendation_shown`, 3,830 `recommendation_clicked`
  (~18.6% of impressions — expected, since only job-type impressions roll a click, and roughly
  30% of impressions are course-type), 1,084 `job_applied` (~28% of clicks — matches the
  `APPLY_RATE_GIVEN_CLICK = 0.30` target), confirming the drop-off funnel behaves as designed
  rather than 1:1:1.
- **Ingestion API**: exercised with real `curl` calls against the running `analytics` container —
  401 with no/wrong key, 201 on a valid single event and on a batch of two, 422 on an unknown
  `event_type`, and a real paginated actor-timeline query returning the inserted rows in
  descending `received_at` order.
- **Instrumentation hook, the real integration point**: called the live matching API's
  `GET /candidates/{id}/job-matches` and confirmed, by querying Postgres before and after, that
  exactly 5 new `recommendation_shown` rows appeared with `source='api'`, correct `rank` values
  (1–5), and `entity_id`s matching the exact job ids the API response returned — not just "an
  event fired," but the right events with the right content.
- **Best-effort degradation, the whole point of the design, proven not assumed**: stopped the
  `analytics` container and re-called the same matching endpoint — it still returned `200` in
  ~0.6s (a refused TCP connection fails fast; it does not hang for the 2-second timeout). The
  matching engine's own behavior is provably unaffected by an analytics outage, not just
  documented as "should be."

## Known limitations (v1, by design)

- No FK-enforced referential integrity between `analytics.behavioral_events` and the matching
  engine's `candidates`/`jobs` tables — a deliberate decoupling choice, checked by a data-quality
  report instead of a constraint.
- Instrumentation call from the matching API to the analytics API is synchronous and best-effort;
  an analytics outage must not break matching responses, but a dropped event is currently silent
  (no retry/dead-letter queue yet).
- No segmentation, clustering, or dashboard yet — this phase only produces the raw event log.
- No real privacy/consent/retention mechanism (see A10) — acceptable only because all data is
  synthetic.
