"""Centralized runtime configuration for every service in this project.

Before this module existed, each service/script re-read `os.environ` on its
own, each with its own copy of the same default literal (e.g. `JME_API_KEY`
had four independent `"dev-local-key-change-me"` fallbacks, `OIDC_CLIENT_ID`
was hardcoded identically in two files with no env override at all). That
made every one of those modules harder to lift into another project: reusing
`app/frontend/oidc.py` elsewhere meant hunting down and editing a hardcoded
constant inside the file, not just setting an env var.

This module fixes that by being the *one* place that reads `os.environ`,
via `pydantic_settings.BaseSettings` (already a dependency of every service
image via `app/requirements-base.txt`). Every other module imports the
`settings` singleton below instead of calling `os.environ.get(...)` itself.

**Env var names and defaults are unchanged** from before this module existed
— `docker-compose.yml` required zero edits — this is a pure consolidation,
not a behavior change. Field names are the lowercased env var name
(`JME_API_KEY` -> `settings.jme_api_key`); `pydantic_settings` matches env
vars to fields case-insensitively, so no per-field aliasing was needed.

Reuse in another project: copy this file, delete the fields your project
doesn't need, add the ones it does. Nothing elsewhere in this module depends
on the specific fields below — `Settings` is a plain `BaseSettings` subclass,
so any project-specific config can follow the exact same pattern (one field
per env var, a sensible local-dev default, a one-line comment on anything
non-obvious) without copying any of this project's actual values.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """One field per environment variable this project reads anywhere.

    Grouped by the subsystem that owns each var, not alphabetically, so the
    relationship between e.g. `oidc_internal_issuer` and `oidc_client_id` is
    visible without cross-referencing other files. Every default here is the
    same local-dev placeholder the file it was moved out of already used —
    see each group's inline comment for where it came from and why that
    default is safe for local dev but must be overridden before any shared
    deployment (docs/assumptions-register.md, assumption A7).
    """

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # --- Database (app/db.py) ------------------------------------------
    database_url: str = "postgresql+psycopg2://jme:jme_dev_password@localhost:5432/jme"

    # --- API-key auth + rate limiting (app/api, app/analytics, app/extraction) --
    # Same key reused across all three services today (see each deps.py's
    # own module docstring for why that's an accepted v1 trade-off).
    jme_api_key: str = "dev-local-key-change-me"
    # None disables Redis-backed rate limiting/caching/streams entirely,
    # falling back to in-memory/synchronous behavior — see each call site's
    # own fail-open handling, unchanged by this module.
    redis_url: str | None = None

    # --- Frontend -> API Gateway routing (app/frontend/*, app/api/analytics_client.py) --
    api_url: str = "http://gateway/matching"
    extraction_url: str = "http://gateway/extraction"
    analytics_url: str = "http://analytics:8001"

    # --- OIDC / Keycloak (app/frontend/oidc.py, keycloak_admin.py, app/api/jwt_auth.py) --
    oidc_internal_issuer: str = "http://keycloak:8080/realms/jme"
    # Previously hardcoded with no env override in two separate files
    # (app/frontend/oidc.py and app/api/jwt_auth.py) — now one field, still
    # defaulting to the same value, but now override-able like every other
    # setting here.
    oidc_client_id: str = "jme-frontend"
    keycloak_admin_client_id: str = "jme-admin"
    keycloak_admin_client_secret: str = ""

    # --- Neo4j (app/graph/neo4j_client.py) ------------------------------
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "dev-local-password-change-me"

    # --- Model serving (app/extraction/ollama_client.py, app/golden/vllm_client.py) --
    ollama_url: str = "http://ollama:11434"
    # Was 60.0, tuned around qwen2.5:1.5b's decode speed on CPU -- bumped
    # to 180.0 when the live chatbot's default model moved to qwen2.5:7b
    # (app/chatbot/graph.py's _LLM_MODEL): confirmed live, a cold model
    # load (not yet resident in Ollama's memory, e.g. after its idle
    # keep-alive evicts it between infrequent requests) plus one 7b
    # generate() call can exceed 60s and hit a real ReadTimeout even
    # though the model itself isn't hung -- see
    # app/extraction/ollama_client.py's own comment for why this is
    # configurable rather than a fixed constant. Shared by the extraction
    # pipeline too (still mostly on the fast 1.5b model there); a longer
    # timeout there only delays detecting a genuine hang, not a
    # correctness change.
    ollama_timeout_seconds: float = 180.0
    vllm_url: str = "http://vllm:8000"
    vllm_model: str = "Qwen/Qwen2.5-1.5B-Instruct"

    # --- CV object storage (app/extraction/document_storage.py) --------
    cv_storage_bucket: str = "candidate-cvs"
    # None (unset) means object storage isn't configured at all — the
    # module this backs treats that as StorageUnavailableError, not a
    # silent no-op, since a CV upload with nowhere to go is a real failure.
    cv_storage_endpoint: str | None = None
    cv_storage_access_key: str | None = None
    cv_storage_secret_key: str | None = None

    # --- MLflow tracking (app/embeddings, app/extraction, app/evaluation eval harnesses) --
    mlflow_tracking_uri: str = "http://localhost:5000"


settings = Settings()
"""Process-wide singleton. Import this, not the `Settings` class, unless a
test needs to construct an isolated override instance (e.g.
`Settings(jme_api_key="test-key")`) instead of monkeypatching `os.environ`."""
