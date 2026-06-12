"""Composition root.

`bootstrap()` wires settings → config → container → services → agent router.
It is the single place that assembles the layers, shared by the HTTP server
(app/server.py) and the CLI tester (app/cli.py) so the two never drift.
"""
from __future__ import annotations

from dotenv import load_dotenv

from app.agent.router import AgentRouter
from app.config import AppConfig, load_config
from app.container import Container
from app.services import register_all
from app.services.registry import ServiceRegistry
from app.settings import Settings


def bootstrap() -> tuple[AppConfig, AgentRouter]:
    """Build the agent router from the environment. Returns (config, router)."""
    load_dotenv()

    settings = Settings.from_env()
    config = load_config(settings.config_path)

    container = Container.build(settings, config)
    registry = ServiceRegistry()
    register_all(container, registry)

    router = AgentRouter.build(settings, config, registry, container)
    return config, router
