# Kubernetes Deployment

Status: v1 — fully deployed and verified on a local cluster; Azure path documented, not
provisioned (see assumption A1 in `docs/assumptions-register.md`).

## Purpose & Scope

Proves the architecture *generalizes* to Kubernetes — the same decoupled images ADR 0001
explicitly anticipated ("migrate becomes an init container or Job... api becomes a horizontally
scalable Deployment... dataquality becomes a Kubernetes CronJob") — without taking on real cloud
cost or account dependency. Per an explicit project decision (resolving assumption A1): **no real
Azure subscription is used anywhere in this project.** Everything here runs against a local
cluster (`kind` — Kubernetes-in-Docker); the Azure-shaped manifests are a documented design,
verified only in the sense that they build into valid YAML, not that they were ever applied to a
real AKS cluster. In scope: local `kind` deployment of every Compose-deployed component, an
Azure-shaped overlay. Out of scope: any real cloud provisioning.

## Architecture & Design

Kustomize (built into `kubectl`), not Helm — this project has one real environment (local) and one
documented-only environment (Azure); Helm's templating earns its complexity at a scale this project
hasn't reached, and overlays make the actual diff between environments explicit and readable in a
way a `values.yaml` often doesn't.

Layout:

```
k8s/
  base/                   # the real, cloud-agnostic resources
    namespace.yaml
    secret.example.yaml   # template — secret.yaml itself is git-ignored, same pattern as .env
    configmaps.yaml       # postgres-init SQL, prometheus.yml, grafana provisioning
    postgres.yaml         # Deployment + PVC + Service (self-hosted, local-only)
    jobs.yaml             # migrate, seed — one-off Jobs with a wait-for-db initContainer
    cronjob-dataquality.yaml
    api.yaml              # Deployment (2 replicas) + Service + HPA
    mlflow.yaml           # Deployment + PVC + Service
    prometheus.yaml       # Deployment + PVC + Service
    grafana.yaml          # Deployment + PVC + Service
  overlays/
    local/                # = base, unpatched (kind's default StorageClass satisfies every PVC)
    azure/                # patches: ACR image names, managed-csi storage class, AGIC Ingress,
                           # deletes the self-hosted postgres (replaced by a managed Azure DB)
```

**Secrets**: `k8s/base/secret.yaml` is git-ignored, same discipline as `.env`. Generate it locally:

```sh
export $(grep -v '^#' .env | xargs)
kubectl create secret generic jme-secrets -n jme \
  --from-literal=POSTGRES_USER="$POSTGRES_USER" \
  --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  --from-literal=POSTGRES_DB="$POSTGRES_DB" \
  --from-literal=MLFLOW_DB_NAME="$MLFLOW_DB_NAME" \
  --from-literal=JME_API_KEY="$JME_API_KEY" \
  --from-literal=GRAFANA_ADMIN_PASSWORD="$GRAFANA_ADMIN_PASSWORD" \
  --from-literal=DATABASE_URL="postgresql+psycopg2://${POSTGRES_USER}:${POSTGRES_PASSWORD}@jme-db:5432/${POSTGRES_DB}" \
  --from-literal=MLFLOW_BACKEND_STORE_URI="postgresql+psycopg2://${POSTGRES_USER}:${POSTGRES_PASSWORD}@jme-db:5432/${MLFLOW_DB_NAME}" \
  --from-literal=MLFLOW_TRACKING_URI="http://jme-mlflow:5000" \
  --dry-run=client -o yaml > k8s/base/secret.yaml
```

`DATABASE_URL`/`MLFLOW_BACKEND_STORE_URI` are pre-composed because Kubernetes Secrets, unlike
Compose's `environment:`, don't support `${VAR}` interpolation.

**Why no separate cAdvisor workload in Kubernetes (unlike Compose)**: Compose needed a standalone
`cadvisor` container for per-container CPU/RAM metrics. A real Kubernetes cluster's kubelet already
exposes equivalent per-container metrics at `/metrics/cadvisor` on every node — no separate
DaemonSet needed. Scraping that endpoint properly requires a `ServiceAccount` + `ClusterRole`
(`nodes`, `nodes/proxy`, `nodes/metrics`) and bearer-token/TLS config in `prometheus.yml` — a real
production addition, deliberately not wired here to keep scope bounded (see Known Limitations).

**Azure-shaped overlay (`k8s/overlays/azure`)** — documented, not deployed. `kubectl kustomize
k8s/overlays/azure` produces valid YAML (verified); it was never `kubectl apply`'d against a real
cluster (no AKS cluster exists for this project). What it changes from `base/`:

| Concern | Local (`base/`) | Azure overlay |
|---|---|---|
| Postgres | Self-hosted `jme-db` Deployment + PVC | **Deleted** — replaced by Azure Database for PostgreSQL Flexible Server (managed; `DATABASE_URL` in the real, never-committed secret would point at its hostname) |
| Container registry | Local Docker daemon / `kind load` | `images:` transformer rewrites all 5 custom images to `myregistry.azurecr.io/...` (placeholder — a real ACR name replaces this) |
| Storage | kind's default `standard` StorageClass | `managed-csi` (Azure Disk CSI) on the remaining PVCs (mlflow/prometheus/grafana) |
| Ingress | None (port-forward only) | `networking.k8s.io/v1` Ingress with AGIC (`azure/application-gateway`) annotations |
| cAdvisor | N/A (not in `base/` either — see above) | N/A |

## Implementation

The base manifests mirror every Compose service 1:1 by role: `migrate`/`seed` become one-off `Job`s
with a `wait-for-db` `initContainer` (`pg_isready` polling loop), `dataquality` becomes a
`CronJob`, `api` becomes a Deployment (2 replicas) + Service + `HorizontalPodAutoscaler`, and
`mlflow`/`prometheus`/`grafana` each get a Deployment + PVC + Service. The `local` overlay applies
`base/` unpatched; the `azure` overlay patches image registries, storage class, Postgres, and adds
an Ingress (see table above).

## Verification & Results

Verified end-to-end on a local `kind` cluster, not assumed:

1. `kind create cluster --name jme-local`
2. `kind load docker-image jme-api:latest jme-migrate:latest jme-seed:latest jme-dataquality:latest jme-mlflow:latest --name jme-local` (plus the 3 third-party images, to avoid relying on the kind node's own network pull)
3. `kubectl apply -k k8s/overlays/local`
4. Confirmed, in order: all 4 PVCs `Bound`; `jme-migrate` Job `Complete` (all 3 Alembic migrations
   ran); `jme-seed` Job `Complete` (2000 candidates / 1000 jobs — confirmed via `psql` inside the
   `jme-db` pod); manually triggered the `jme-dataquality` CronJob
   (`kubectl create job --from=cronjob/jme-dataquality`) — 10/10 checks passed; `jme-api`
   Deployment running 2/2 pods; a real authenticated call through the Service (port-forwarded)
   returned the same ranked-match output already verified against Compose; Prometheus reports the
   `api` scrape target `up`; Grafana's "JME Overview" dashboard is provisioned correctly (verified
   via its own API, the same way it was verified under Compose).

**Real bugs found and fixed while verifying this (not assumed to work)**:
- **Grafana crash-looped** (`not a directory` mount error) on the first apply: two separate
  ConfigMaps (`grafana-dashboard-provider`, `grafana-dashboard-json`) were both mounted into
  `/etc/grafana/provisioning/dashboards` — one as the whole directory, one via `subPath` for a
  single file — which conflicts at the container runtime level. Fixed by merging them into one
  `grafana-dashboards` ConfigMap (both files are just two keys in the same ConfigMap, each
  projected as a file in the mounted directory) — no `subPath` needed at all.
- **`jme-migrate`/`jme-seed` Jobs failed their first attempts**: Compose's `depends_on: condition:
  service_healthy` has no direct Kubernetes equivalent for a Job waiting on a Deployment's
  readiness. Both Jobs raced ahead of Postgres (still pulling its image) and errored. Fixed with a
  `wait-for-db` `initContainer` (`pg_isready` polling loop) ahead of the real container — the
  standard Kubernetes pattern for this, not previously needed under Compose.
- `jme-seed`'s very first retry attempt still failed once (`relation "job_skills" does not
  exist`) because it ran before `jme-migrate` finished — the two Jobs have no ordering dependency
  on *each other*, only on the database being reachable. This mirrors Compose exactly (seed and
  migrate were always manually sequenced there too — see ADR 0001), so it's left as-is, not
  treated as a new gap introduced by Kubernetes — `backoffLimit: 4` absorbs it.

## Known Limitations

- **HPA is a no-op here**: `k8s/base/api.yaml`'s `HorizontalPodAutoscaler` is applied and visible
  (`kubectl get hpa` shows it), but reports `cpu: <unknown>/70%` because `metrics-server` isn't
  installed on this `kind` cluster (not part of `kind` by default, a real addition). It demonstrates
  the intended design, not a verified autoscaling behavior.
- **No kubelet-cadvisor RBAC scraping** — see Architecture & Design above.
- **Azure overlay unverified against real infrastructure** — see assumption A1. A real AKS
  deployment would likely surface issues this local test can't: IAM/managed-identity wiring for
  ACR pull and Azure Database access, Application Gateway provisioning, egress/networking rules.
- **No Helm** — a deliberate choice (see Architecture & Design above), reconsider only if a third
  real environment needs config this different from local/Azure that overlays stop being the
  simpler option.
- **Manifests are stale relative to the full Docker Compose stack**: `k8s/base/` only covers `api`,
  `postgres`, `mlflow`, `prometheus`, `grafana`, plus the `migrate`/`seed`/`dataquality` jobs — the
  deployment this doc describes predates `redis` (Phase 21), `extraction`/`ollama` (Phase 15-16),
  `neo4j` (Phase 19), `seaweedfs` (Phase 26), and `frontend` (Phase 27), none of which have a
  Kubernetes manifest today. `api` is the only service with any replica/autoscaling config at all;
  everything added since Phase 11 only exists as a single Docker Compose container locally, with no
  scaling path defined anywhere. Not a deliberate scope decision — just not revisited since.

## Related Documents

- `docs/architecture.md` — overview and deployment view
- `docs/progress.md` — full build history
- `docs/infrastructure-sizing.md` — resource allocation for the `api` Deployment and the Azure cost
  estimate derived from this overlay
- `docs/assumptions-register.md` — assumption A1 (no real Azure subscription)
- `docs/adr/0001-decouple-ingestion-migrate-api.md` — the service-decoupling decision this
  deployment model depends on
