from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import router as matching_router

app = FastAPI(title="JME API", version="0.1.0")
app.include_router(matching_router)

# Exposes /metrics (request count, latency histograms, in-progress requests,
# all labeled by path/method/status) for Prometheus to scrape - the
# latency/error-rate half of the Phase 9 observability requirement.
# CPU/RAM come from cAdvisor instead (container-level, not app-level - see
# docker-compose.yml and docs/observability.md).
Instrumentator().instrument(app).expose(app)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/readiness")
def readiness():
    return {"status": "ready"}
