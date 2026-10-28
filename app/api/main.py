from fastapi import FastAPI

app = FastAPI(title="JME API", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/readiness")
def readiness():
    return {"status": "ready"}
