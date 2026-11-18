"""Thin Neo4j driver wrapper (Phase 19). Connection config from env vars,
consistent with every other service's env-var-driven configuration in this
project.
"""
from neo4j import Driver, GraphDatabase

from app.common.config import settings

NEO4J_URI = settings.neo4j_uri
NEO4J_USER = settings.neo4j_user
NEO4J_PASSWORD = settings.neo4j_password


def get_driver() -> Driver:
    """Builds a new Neo4j `Driver` from `settings.neo4j_{uri,user,password}`.

    Returns:
        neo4j.Driver: an unconnected driver — callers open sessions from it
            (`driver.session()`) and are responsible for closing the driver
            when done (e.g. via a `with` block or `driver.close()`).
    """
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
