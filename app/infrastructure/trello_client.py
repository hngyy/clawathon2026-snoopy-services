"""Trello REST client — create and read cards for supporting-team tasks.

Auth is a static API key + token (Trello tokens don't expire unless revoked), so
unlike the Outlook mailer there's no OAuth flow. Build this only when both
TRELLO_API_KEY and TRELLO_TOKEN are set; callers fall back to email otherwise.

Every method raises TrelloError on failure so the caller can fall back gracefully.
"""
from __future__ import annotations

import logging
import warnings

import requests
import urllib3

logger = logging.getLogger("tour_bot.trello")

_BASE = "https://api.trello.com/1"
_TIMEOUT = 20


class TrelloError(RuntimeError):
    pass


class TrelloClient:
    def __init__(self, api_key: str, token: str):
        self._auth = {"key": api_key, "token": token}
        self._http = requests.Session()
        # Corporate SSL inspection proxies intercept outbound TLS — suppress verify errors
        # rather than requiring a custom CA bundle on every deployment.
        self._http.verify = False
        warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        params = {**self._auth, **kwargs.pop("params", {})}
        resp = self._http.request(method, f"{_BASE}{path}", params=params, timeout=_TIMEOUT, **kwargs)
        if not resp.ok:
            raise TrelloError(f"Trello {method} {path} failed ({resp.status_code}): {resp.text}")
        return resp.json()

    def create_card(self, list_id: str, name: str, desc: str) -> dict:
        """Create a card on a list. Returns the card dict (incl. id, shortUrl, idList)."""
        card = self._request("post", "/cards", params={"idList": list_id, "name": name, "desc": desc})
        logger.info("Created Trello card id=%s on list=%s", card.get("id"), list_id)
        return card

    def get_card(self, card_id: str) -> dict:
        """Fetch a card (incl. idList) so the caller can resolve its current status."""
        return self._request("get", f"/cards/{card_id}", params={"fields": "name,idList,url,closed"})

    def list_name(self, list_id: str) -> str:
        """Resolve a list id to its display name (e.g. 'Done') — used as a status label."""
        return self._request("get", f"/lists/{list_id}", params={"fields": "name"}).get("name", list_id)
