"""Configuration loaded from environment / .env.

All secrets and tunables live here so the rest of the code never reads
``os.environ`` directly. See ``.env.example`` for the full list.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # GitHub App
    github_app_id: str = ""
    github_private_key: str = ""
    github_private_key_path: str = ""
    github_webhook_secret: str = ""
    github_api_url: str = "https://api.github.com"

    # LLM
    openai_api_key: str = ""
    llm_model: str = "gpt-4o"
    dry_run: bool = False

    # code-review-graph limits (passed through to the CLI subprocess env)
    crg_max_changed_funcs: int = 500
    crg_max_transitive_frontier: int = 2000
    crg_tool_timeout: int = 120
    crg_git_timeout: int = 60

    # Service
    work_root: str = "/tmp/pr-review-bot"
    log_level: str = "INFO"

    def resolve_private_key(self) -> str:
        """Return the App private key PEM, reading from disk if a path is set.

        ``GITHUB_PRIVATE_KEY_PATH`` takes precedence; otherwise the inline
        ``GITHUB_PRIVATE_KEY`` is used (with literal ``\\n`` un-escaped so the
        key can live on a single env line).
        """
        if self.github_private_key_path:
            return Path(self.github_private_key_path).read_text(encoding="utf-8")
        return self.github_private_key.replace("\\n", "\n")

    def crg_env(self) -> dict[str, str]:
        """Environment overrides applied to every code-review-graph call."""
        return {
            "CRG_MAX_CHANGED_FUNCS": str(self.crg_max_changed_funcs),
            "CRG_MAX_TRANSITIVE_FRONTIER": str(self.crg_max_transitive_frontier),
            "CRG_TOOL_TIMEOUT": str(self.crg_tool_timeout),
            "CRG_GIT_TIMEOUT": str(self.crg_git_timeout),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
