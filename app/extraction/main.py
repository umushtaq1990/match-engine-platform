import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.extraction.routes import router as extraction_router

app = FastAPI(title="JME Skill Extraction", version="0.1.0")
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
def health():
    return {"status": "ok"}


@app.get("/readiness")
def readiness():
    return {"status": "ready"}
