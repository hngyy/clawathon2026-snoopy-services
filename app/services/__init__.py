"""Service registration entrypoint.

`register_all` runs every service's `register(container, registry)`.
To add a new service: create app/services/<name>/ with an __init__.py
exposing register(container, registry), then add it to REGISTRARS here.
"""
from __future__ import annotations

from app.container import Container
from app.services import tour_visit
from app.services.registry import ServiceRegistry

# Each entry is a module/package exposing register(container, registry).
REGISTRARS = [tour_visit]


def register_all(container: Container, registry: ServiceRegistry) -> None:
    for module in REGISTRARS:
        module.register(container, registry)
