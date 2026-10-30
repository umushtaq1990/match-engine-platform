"""Thin Neo4j driver wrapper (Phase 19). Connection config from env vars,
consistent with every other service's env-var-driven configuration in this
project.
"""
import os

from neo4j import Driver, GraphDatabase

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "dev-local-password-change-me")


def get_driver() -> Driver:
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
