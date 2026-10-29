-- Runs automatically on first init of a fresh Postgres volume only.
-- Creates a separate database for MLflow's backend store, kept distinct
-- from the "jme" application database. The literal name here must match
-- MLFLOW_DB_NAME in .env (default "mlflow") — init scripts can't read
-- compose env vars, so if MLFLOW_DB_NAME is ever changed, update this file
-- too.
CREATE DATABASE mlflow;
