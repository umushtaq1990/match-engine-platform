import os

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.responses import Response

from app.api.routes import router as matching_router

app = FastAPI(title="JME API", version="0.1.0")
app.include_router(matching_router)

# Exposes /metrics (request count, latency histograms, in-progress requests,
# all labeled by path/method/status) for Prometheus to scrape - the
# latency/error-rate half of the Phase 9 observability requirement.
# CPU/RAM come from cAdvisor instead (container-level, not app-level - see
# docker-compose.yml and docs/observability.md).
#
# Phase 13: running multiple uvicorn workers (see Dockerfile.api) means each
# worker has its own in-memory metric counters - the default .expose()
# would only ever show whichever single worker happened to answer the
# scrape (found and verified by load-testing: /metrics under repeated
# scraping showed the count alternating, not accumulating). Fixed with
# prometheus_client's documented multiprocess mode: workers write to
# PROMETHEUS_MULTIPROC_DIR, and /metrics aggregates across all of them.
instrumentator = Instrumentator().instrument(app)

if "PROMETHEUS_MULTIPROC_DIR" in os.environ:
    from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest, multiprocess

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
else:
    instrumentator.expose(app)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/readiness")
def readiness():
    return {"status": "ready"}
