"""LLM client factory (any OpenAI-compatible provider: GreenNode AIP, OpenAI, …)."""
from __future__ import annotations

import httpx
from langchain_openai import ChatOpenAI

from app.settings import Settings

# Corporate/self-signed SSL proxies require verify=False for local dev.
# In production (AgentBase runtime), the container trusts the CA chain directly.
_HTTP_CLIENT = httpx.Client(verify=False)
_HTTP_ASYNC_CLIENT = httpx.AsyncClient(verify=False)


def build_llm(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        openai_api_base=settings.llm_base_url,
        openai_api_key=settings.llm_api_key,
        http_client=_HTTP_CLIENT,
        http_async_client=_HTTP_ASYNC_CLIENT,
    )
