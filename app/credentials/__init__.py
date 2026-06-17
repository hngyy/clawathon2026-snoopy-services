"""Credentials package — per-service config files and the loader that reads them.

Each external integration keeps its secrets in its own `<service>.credentials.json`
inside this directory (all git/Docker-ignored via the `*.credentials.json` rule),
mirroring the `.greennode.json` convention. OAuth token caches live under `cache/`.

Resolution priority per field — the same as the GreenNode SDK: **env → file → None**.
This lets local dev use the JSON files while a deployed runtime can inject the same
values as environment variables (secret injection) with no code change.

Paths are resolved relative to this package, so loading works regardless of CWD.

    from app.credentials import load_credentials, CACHE_DIR
    creds = load_credentials("trello", {"api_key": "TRELLO_API_KEY", "token": "TRELLO_TOKEN"})
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CREDENTIALS_DIR = Path(__file__).resolve().parent
CACHE_DIR = CREDENTIALS_DIR / "cache"


def load_credentials(
    service: str,
    fields: dict[str, str],
    *,
    base_dir: Path | None = None,
) -> dict[str, str | None]:
    """Resolve a service's credential fields.

    Args:
        service: file stem — reads ``<base_dir>/<service>.credentials.json``.
        fields:  maps logical field name → environment variable name.
        base_dir: directory the credential file lives in (default: this package).

    Returns:
        {field: value} where each value is the env var if set, else the file
        value, else None. Missing/invalid file is treated as empty (no error).
    """
    directory = base_dir or CREDENTIALS_DIR
    path = directory / f"{service}.credentials.json"
    data: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError):
            data = {}

    resolved: dict[str, str | None] = {}
    for field, env_var in fields.items():
        value = os.environ.get(env_var) or data.get(field)
        resolved[field] = value or None
    return resolved
