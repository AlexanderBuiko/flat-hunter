"""Environment configuration (read once, at process start)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    bot_token: str
    allowed_user_ids: set[str]
    db_path: str
    interval_s: int
    provider: str          # LLM provider for extraction / NL→schema
    user_rate_max: int     # max LLM-triggering actions per user per window (flood cap)
    user_rate_window_s: int  # the window that ``user_rate_max`` is counted over, seconds

    @classmethod
    def from_env(cls) -> "Config":
        ids = os.environ.get("FLAT_HUNTER_ALLOWED_USER_IDS", "")
        return cls(
            bot_token=os.environ.get("FLAT_HUNTER_BOT_TOKEN", "").strip(),
            allowed_user_ids={i.strip() for i in ids.split(",") if i.strip()},
            db_path=os.environ.get("FLAT_HUNTER_DB", "flat_hunter.db"),
            interval_s=int(os.environ.get("FLAT_HUNTER_INTERVAL_S", "10800")),
            provider=os.environ.get("JARVIS_LLM_PROVIDER", "ollama"),
            user_rate_max=int(os.environ.get("FLAT_HUNTER_USER_RATE", "20")),
            user_rate_window_s=int(os.environ.get("FLAT_HUNTER_USER_RATE_WINDOW_S", "3600")),
        )

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.allowed_user_ids)
