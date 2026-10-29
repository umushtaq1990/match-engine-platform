-- Runs automatically on first init of a fresh Postgres volume only.
-- Creates a separate database for MLflow's backend store, kept distinct
-- from the "jme" application database.
CREATE DATABASE mlflow;
