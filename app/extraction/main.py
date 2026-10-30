import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.extraction.rate_limit import limiter
from app.extraction.routes import router as extraction_router

app = FastAPI(title="JME Skill Extraction", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.include_router(extraction_router)

Instrumentator().instrument(app).expose(app)


# Unlike app/api/analytics_client.py, a failure calling Ollama is a real
# error here, not something to swallow — the caller has nothing useful
# without a result (docs/skill-extraction.md "Service boundary"). Surfaced
# as 502, not a generic 500, so callers can tell "the model service is down"
# from "this request was malformed."
@app.exception_handler(httpx.HTTPError)
def _model_service_unavailable(request: Request, exc: httpx.HTTPError):
    return JSONResponse(status_code=502, content={"detail": "Model-serving service unavailable"})


@app.get("/health")
@limiter.exempt
def health():
    return {"status": "ok"}


@app.get("/readiness")
@limiter.exempt
def readiness():
    return {"status": "ready"}
