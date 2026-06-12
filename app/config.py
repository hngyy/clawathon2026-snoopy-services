"""Loads config.yaml into typed objects.

AppConfig is intentionally service-agnostic: it holds shared platform concerns
(email sender, routing topology, role resolution) and exposes raw service dicts
via `.service(key)`. Each service module parses its own slice in its own config.py.

Roles are data-driven: each role may declare `identities` (user ids that get it),
`extends` (inherit another role's tools + prompts), and one role is the `default`
for unmatched users. Adding a role is a config + service-binding change — no code.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_VALID_ROUTING = ("flat", "supervisor")


@dataclass(frozen=True)
class EmailConfig:
    from_address: str
    from_name: str


@dataclass(frozen=True)
class Role:
    name: str
    identities: tuple
    extends: str | None
    is_default: bool


@dataclass(frozen=True)
class AppConfig:
    email: EmailConfig
    routing: str
    _roles: dict
    _default_role: str
    _services: dict

    def role_for(self, user_id: str | None) -> str:
        """Resolve a caller's role from their user id, falling back to the default."""
        if user_id:
            uid = user_id.lower()
            for role in self._roles.values():
                if role.name != self._default_role and uid in {i.lower() for i in role.identities}:
                    return role.name
        return self._default_role

    def role_chain(self, role: str) -> list[str]:
        """Return [role, parent, grandparent, …] following `extends`. Cycle-safe."""
        chain, seen = [], set()
        current: str | None = role
        while current and current in self._roles and current not in seen:
            seen.add(current)
            chain.append(current)
            current = self._roles[current].extends
        return chain

    def roles(self) -> list[str]:
        return list(self._roles.keys())

    @property
    def default_role(self) -> str:
        return self._default_role

    def service(self, key: str) -> dict:
        """Return raw config dict for a service, or {} if not present."""
        return self._services.get(key, {})


def _load_roles(raw: dict) -> tuple[dict, str]:
    roles_raw = raw.get("roles", {}) or {}
    if not roles_raw:
        raise ValueError("config.yaml must define at least one role under `roles:`.")

    roles: dict[str, Role] = {}
    defaults = []
    for name, r in roles_raw.items():
        r = r or {}
        is_default = bool(r.get("default", False))
        if is_default:
            defaults.append(name)
        roles[name] = Role(
            name=name,
            identities=tuple(r.get("identities", [])),
            extends=r.get("extends"),
            is_default=is_default,
        )

    if len(defaults) != 1:
        raise ValueError(
            f"Exactly one role must set `default: true` (found {len(defaults)}: {defaults})."
        )
    for role in roles.values():
        if role.extends and role.extends not in roles:
            raise ValueError(f"Role '{role.name}' extends unknown role '{role.extends}'.")

    return roles, defaults[0]


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found at '{path}'. Set CONFIG_PATH or create config.yaml."
        )
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    routing = raw.get("routing", "flat")
    if routing not in _VALID_ROUTING:
        raise ValueError(
            f"Invalid routing '{routing}'. Must be one of: {', '.join(_VALID_ROUTING)}."
        )

    roles, default_role = _load_roles(raw)

    email_raw = raw.get("email", {})
    return AppConfig(
        email=EmailConfig(
            from_address=email_raw.get("from_address", "no-reply@example.com"),
            from_name=email_raw.get("from_name", "Campus Bot"),
        ),
        routing=routing,
        _roles=roles,
        _default_role=default_role,
        _services=raw.get("services", {}),
    )
