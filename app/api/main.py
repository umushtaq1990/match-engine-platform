from fastapi import FastAPI

from app.api.routes import router as matching_router

app = FastAPI(title="JME API", version="0.1.0")
app.include_router(matching_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/readiness")
def readiness():
    return {"status": "ready"}
