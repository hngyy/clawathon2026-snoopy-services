"""Runtime owner elevation for the Zalo bot.

Lets a Zalo user become the `owner` role in-chat via `/owner <password>` without
editing config.yaml or redeploying. Elevated Zalo sender ids are persisted to a
small JSON file under data_dir so they survive within a running container.

Caveat: the runtime filesystem is ephemeral — a redeploy (new image) or pod
restart starts fresh, so owners must re-run `/owner <password>` afterwards. The
password itself lives in env (ZALO_OWNER_PASSWORD), injected at deploy time.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger("tour_bot.zalo")


class OwnerStore:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._ids: set[str] = self._load()

    def _load(self) -> set[str]:
        if not self._path.exists():
            return set()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return set(map(str, data)) if isinstance(data, list) else set()
        except (json.JSONDecodeError, OSError):
            return set()

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(sorted(self._ids)), encoding="utf-8")
        except OSError:
            logger.warning("Could not persist Zalo owners to %s", self._path)

    def is_owner(self, sender_id: str) -> bool:
        return sender_id in self._ids

    def add(self, sender_id: str) -> None:
        with self._lock:
            self._ids.add(sender_id)
            self._save()
        logger.warning("Zalo sender elevated to owner: %s", sender_id)

    def remove(self, sender_id: str) -> None:
        with self._lock:
            self._ids.discard(sender_id)
            self._save()
        logger.warning("Zalo sender de-elevated to requester: %s", sender_id)
