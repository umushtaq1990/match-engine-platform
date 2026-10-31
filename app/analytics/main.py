from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.analytics import stream_consumer
from app.analytics.rate_limit import limiter
from app.analytics.routes import router as events_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 21: background Redis Streams consumer for the matching API's
    # recommendation_shown instrumentation hook. No-op if REDIS_URL isn't
    # set — see app/analytics/stream_consumer.py. The existing /events and
    # /events/batch HTTP endpoints below stay as-is for direct submission.
    stream_consumer.start()
    yield
    stream_consumer.stop()


app = FastAPI(title="JME Analytics", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.include_router(events_router)

# Single worker process for v1 (see docker/Dockerfile.analytics) — no
# multiprocess metrics fix needed yet. That fix (app/api/main.py) was only
# required once the matching API moved to --workers 4 under load (Phase 13).
# Revisit if/when this service also needs multiple workers.
Instrumentator().instrument(app).expose(app)


@app.get("/health")
@limiter.exempt
def health():
    return {"status": "ok"}


@app.get("/readiness")
@limiter.exempt
def readiness():
    return {"status": "ready"}
