"""Environment-driven settings (LLM provider + general paths).

Kept separate from `config.py` (which reads config.yaml): env holds secrets and
deployment wiring, config.yaml holds business configuration. Nothing here is
service-specific — services derive their own paths under `data_dir`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    config_path: Path
    data_dir: Path
    outbox_path: Path
    memory_id: str | None
    memory_strategy_id: str | None

    @property
    def memory_enabled(self) -> bool:
        return bool(self.memory_id and self.memory_strategy_id)

    @classmethod
    def from_env(cls) -> "Settings":
        llm_model = os.environ.get("LLM_MODEL", "")
        llm_base_url = os.environ.get("LLM_BASE_URL", "")
        llm_api_key = os.environ.get("LLM_API_KEY", "")
        if not (llm_model and llm_base_url and llm_api_key):
            raise ValueError(
                "LLM_MODEL, LLM_BASE_URL, and LLM_API_KEY are required. "
                "Set them in .env or use /agentbase-llm to get a platform API key."
            )
        return cls(
            llm_model=llm_model,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            config_path=Path(os.environ.get("CONFIG_PATH", "config.yaml")),
            data_dir=Path(os.environ.get("DATA_DIR", ".")),
            outbox_path=Path(os.environ.get("OUTBOX_PATH", "outbox.log")),
            memory_id=os.environ.get("MEMORY_ID") or None,
            memory_strategy_id=os.environ.get("MEMORY_STRATEGY_ID") or None,
        )
