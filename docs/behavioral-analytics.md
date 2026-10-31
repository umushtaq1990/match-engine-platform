# Behavioral Analytics & User Engagement Tracking

Status: v2 — **implemented and verified end-to-end**: ingestion API, storage, an async
Redis Streams-decoupled instrumentation hook, synthetic event generator, and rule-based engagement
segmentation. ML clustering (v3) remains design-only.

## Purpose & Scope

Track three kinds of actor behavior against the existing candidate/job/course domain and turn that
log into actionable engagement signals:
- **Impressions**: a recommendation was shown (candidate saw ranked jobs, employer saw ranked
  candidates, candidate saw course recommendations).
- **Interactions**: the actor clicked/opened/saved/applied in response.
- **Session context**: enough to reconstruct "shown → clicked → applied" funnels per actor, per
  session.
- **Engagement segmentation**: rolling up that event history into a rule-based label per actor
  (`active_seeker` / `passive_seeker` / `dormant`).

Explicitly **not** in scope yet: ML clustering, a dashboard beyond Grafana's existing panels, and
any real privacy/consent mechanism (synthetic data only — see "Known Limitations").

### Why this is a separate service, not a feature of the matching engine

The matching engine is low-volume and read-heavy: a case worker asks for ranked matches a handful
of times a session. Behavioral tracking is the opposite shape — high-volume, append-only,
fire-and-forget writes (every impression, every click) that must never slow down or block a
request that's actually serving a user. Those two workloads have different scaling, ownership, and
failure-tolerance profiles, and in a real organization they're usually owned by different teams
(platform/matching vs. growth/analytics).

So this is modeled as its own bounded context from day one — its own image, own schema, own API —
the same separation-of-concerns principle already applied to `ingestion`/`migrate`/`api` (see
`docs/adr/0001-decouple-ingestion-migrate-api.md`), just drawn one level higher, between *products*
rather than between steps of one pipeline.

## Architecture & Design

### Service boundary

| | Decision | Rationale |
|---|---|---|
| Service | New `analytics` FastAPI service, own `docker/Dockerfile.analytics`, own `app/analytics/` package | Independently deployable/scalable; no coupling to `app/matching` or `app/api` code |
| Storage | Same Postgres **instance**, separate **schema** (`analytics`) | Cheapest way to get real bounded-context separation (own migrations, own tables, no cross-schema FK) without standing up a second database container before there's a reason to. A dedicated event store (or ClickHouse-style columnar store for analytical queries at scale) is a documented future step, not built now — same "prove the simple thing first" rule as everything else in this project |
| Cross-service reference | `actor_id`/`entity_id` are **plain integers, no FK constraint** to `candidates`/`jobs` | A genuine architectural choice, not an oversight: a hard FK would couple the analytics schema's migrations to the matching engine's table lifecycle. Referential integrity is checked by an analytics-side data-quality check instead, which can *report* an orphan reference without *blocking* on one |
| Auth | Same `X-API-Key` pattern as the matching API | Consistent with the existing security model; no new auth mechanism invented for one more service |

### Why `recommendation_shown` must be automatic, not client-reported

Clicks and applies are naturally reported by whatever calls the API. Impressions are not — nothing
client-side has a reason to tell you "I was shown item #3 at rank 3 with score 0.71" unless you
make it. Without a logged impression, a later click has no denominator, and click-through-rate
(clicks ÷ impressions) — one of the most basic and valuable metrics this whole subsystem exists to
produce — becomes uncomputable retroactively.

So the instrumentation point is in `app/api/routes.py`, not in a client SDK: every response from
`GET /candidates/{id}/job-matches`, `GET /jobs/{id}/candidate-matches`, and
`GET /candidates/{id}/course-recommendations` also fires one `recommendation_shown` event per
returned item, via a batch call (so N returned items cost one call to the analytics service, not
N).

**Decoupling, from synchronous to asynchronous**: the matching API originally called the analytics
API in-line, logging and swallowing a failure (never letting an analytics outage break a real
matching response) but with no retry. It now publishes via Redis Streams instead
(`app/api/stream_client.py`, `app/analytics/stream_consumer.py` — see `docs/redis-infrastructure.md`
"Streams" for the broker-level design): an `XADD` instead of a direct HTTP call, durable across an
analytics outage (the stream persists events up to a configured `MAXLEN` even while the consumer is
down — strictly better than the old "down means dropped"), falling back to the original synchronous
HTTP path only when `REDIS_URL` isn't configured at all. The `POST /events`/`/events/batch` HTTP
endpoints remain unchanged and still used for direct/manual submission — both paths coexist.

### Engagement segmentation design

`app/analytics/segmentation.py` rolls up three RFM-style features — computed in one aggregate SQL
query (conditional `CASE WHEN` counts, not several round trips, the same N+1-avoidance discipline
used elsewhere in this project) — into a rule-based label, mirroring assumption A5's "rule-based
baseline before ML" principle:

- **Recency**: days since the actor's most recent event (any type), via `received_at` — the
  server-set, authoritative timestamp (see "Event schema" below), not the client-supplied
  `occurred_at`.
- **Frequency**: event count in a rolling 30-day window.
- **Conversion** (the RFM "monetary" analog here): `job_applied` ÷ `recommendation_shown` counts.

**Thresholds (documented, not hidden)**: no event in 30+ days → `dormant`, regardless of past
volume. Otherwise, `>=5` events in the window *or* `>=50%` conversion → `active_seeker`; anything
else recent → `passive_seeker`. Round, interpretable numbers chosen up front, not fit to this one
synthetic dataset.

## Implementation

### Event schema

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

**Event taxonomy**:

| event_type | entity_type | metadata (example) | Emitted by |
|---|---|---|---|
| `recommendation_shown` | job / candidate / course | `{"rank": 3, "score": 0.71, "algorithm_version": "rule-based-v1"}` | Instrumentation hook in the existing matching API (automatic, not client-initiated) |
| `recommendation_clicked` | job / candidate / course | `{"rank": 3}` | Client calls `POST /events` |
| `job_applied` | job | `{}` | Client calls `POST /events` |
| `job_saved` | job | `{}` | Client calls `POST /events` |
| `search_performed` | — | `{"query": "...", "result_count": 12}` | Client calls `POST /events` |
| `login` | — | `{}` | Client calls `POST /events` |

`algorithm_version` on `recommendation_shown` is included deliberately: it's what lets a future A/B
comparison ("did the embedding-based matcher get clicked more than the rule-based one?") be
answered from this table, without redesigning it.

### API contract

| Endpoint | Purpose |
|---|---|
| `POST /events` | Ingest one client-reported event |
| `POST /events/batch` | Ingest many events in one call (used by the instrumentation hook, and available to any future client) |
| `GET /actors/{actor_type}/{actor_id}/events?event_type=&limit=` | Raw, paginated timeline for one actor — debugging/demo use, not a product endpoint |
| `GET /actors/{actor_type}/{actor_id}/segment` | Rule-based engagement segmentation (see "Architecture & Design" above) |

All require `X-API-Key`, same as the matching API.

### Synthetic event generator

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

## Verification & Results

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
- **Engagement segmentation — a real, honest finding from running it against the actual synthetic
  data**: segmenting the first 200 candidates (and separately the first 200 employers/jobs)
  produced **zero** `passive_seeker` labels — a clean 100/100 active/dormant split for candidates,
  63/137 for employers. Traced to the synthetic event generator's own structure
  (`app/analytics/generate_synthetic_events.py`): each simulated "session" is one atomic burst — a
  login plus `TOP_K` `recommendation_shown` events (plus possible clicks/applies) all at the same
  `occurred_at` — rather than independently-varying recency and frequency. The moment a candidate's
  most recent session lands inside the 30-day window, that same session has already produced well
  over 5 events, clearing the `active_seeker` threshold immediately. Recency and frequency are
  strongly correlated by construction in this generator, which collapses the three-label space into
  two real buckets for this dataset. This is a property of the synthetic data, not a bug in the
  segmentation logic — the thresholds and the aggregate query are both independently unit- and
  DB-tested (`tests/analytics/test_segmentation.py`, `test_segmentation_db.py`) against
  hand-constructed event sequences that **do** produce all three labels, including
  `passive_seeker`.

## Known Limitations

- No FK-enforced referential integrity between `analytics.behavioral_events` and the matching
  engine's `candidates`/`jobs` tables — a deliberate decoupling choice, checked by a data-quality
  report instead of a constraint.
- Instrumentation delivery is durable via Redis Streams, but there's still no retry/dead-letter
  handling for a consumer that crashes mid-batch — see `docs/redis-infrastructure.md` "Known
  Limitations".
- No real clustering yet — segmentation is rule-based only (see "Roadmap").
- No real privacy/consent/retention mechanism (see assumption A10 in
  `docs/assumptions-register.md`) — acceptable only because all data is synthetic; a real
  deployment of this subsystem would need an explicit lawful basis/consent mechanism,
  pseudonymization or anonymization options, and a data-retention/deletion policy before tracking
  real user behavior.

## Roadmap

- **ML clustering, only if rule-based segmentation is shown insufficient**: real clustering
  (k-means/HDBSCAN) over engagement feature vectors, evaluated against the current rule-based
  labels the same way `docs/evaluation-strategy.md` evaluates matching quality — a measured
  comparison, not an assumed improvement.
- A Grafana panel reusing the existing observability stack, an analytics-specific data-quality
  `CronJob` mirroring the matching engine's pattern, and consumer-group failure handling
  (`XPENDING`/claim logic for a crashed consumer) remain open — see
  `docs/redis-infrastructure.md` "Known Limitations" for the Streams-specific gap.

## Related Documents

- [`docs/redis-infrastructure.md`](redis-infrastructure.md) — the Streams broker this service's
  instrumentation hook and consumer depend on.
- [`docs/data-quality.md`](data-quality.md) — the orphan-reference check pattern reused here.
- [`docs/evaluation-strategy.md`](evaluation-strategy.md) — the evaluation methodology any future
  ML clustering comparison would follow.
- [`docs/assumptions-register.md`](assumptions-register.md) — assumption A10 (privacy/consent).
- [`docs/adr/0001-decouple-ingestion-migrate-api.md`](adr/0001-decouple-ingestion-migrate-api.md) —
  the service-boundary precedent this design follows.
- [`docs/architecture.md`](architecture.md) — system-wide overview.
- [`docs/progress.md`](progress.md) — full build history.
