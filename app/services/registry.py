"""Service registry — what makes the bot multi-service.

A Service bundles a key, display name, description, and role-keyed bindings:
  tools[role]   → the tools that role may call for this service
  prompts[role] → the prompt section shown to that role for this service

Roles are arbitrary strings (e.g. "requester", "owner") defined in config.yaml.
The registry is an instance (not module globals) so each app build gets its own.

To add a new service:
  1. Create app/services/<name>/ with __init__.py exposing register(container, registry).
  2. Add it to REGISTRARS in app/services/__init__.py.
No change to the agent or server is needed.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Service:
    key: str
    display_name: str
    description: str = ""  # one line — what the supervisor classifier routes on
    tools: dict = field(default_factory=dict)    # role -> list of tools
    prompts: dict = field(default_factory=dict)  # role -> prompt section string


class ServiceRegistry:
    def __init__(self):
        self._services: dict = {}

    def add(self, service: Service) -> None:
        self._services[service.key] = service

    def services(self) -> list:
        return list(self._services.values())

    def tools_for(self, role: str) -> list:
        return [t for s in self._services.values() for t in s.tools.get(role, [])]

    def prompts_for(self, role: str) -> list[str]:
        return [s.prompts[role] for s in self._services.values() if s.prompts.get(role)]
