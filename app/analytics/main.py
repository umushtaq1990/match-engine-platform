from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.analytics.routes import router as events_router

app = FastAPI(title="JME Analytics", version="0.1.0")
app.include_router(events_router)

# Single worker process for v1 (see docker/Dockerfile.analytics) — no
# multiprocess metrics fix needed yet. That fix (app/api/main.py) was only
# required once the matching API moved to --workers 4 under load (Phase 13).
# Revisit if/when this service also needs multiple workers.
Instrumentator().instrument(app).expose(app)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/readiness")
def readiness():
    return {"status": "ready"}
