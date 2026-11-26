"""What a chat turn is run with: whose it is, where its data lives and which models answer."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class PMConfig:
    user_id: str = ""             # owner identity (UUID from users table)
    provider: str = "openai"
    model: str = "gpt-oss:120b"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    data_dir: str = "./data"
    vault_dir: Optional[str] = None
    session_id: str = "default"   # chat thread identifier
    on_progress: Optional[Callable[[str], None]] = None


@dataclass
class OrchestratorConfig:
    session_id: str           # chat thread identifier
    user_id: str = ""         # owner identity (from auth cookie)
    data_dir: str = "./data"
    vault_dir: Optional[str] = None
    # Orchestrator model (reasoning + humanization)
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    # PM agent model (fast, cheap execution)
    pm_provider: str = "openai"
    pm_model: str = "gpt-4o-mini"
    pm_api_key: Optional[str] = None
    pm_base_url: Optional[str] = None

    def pm_config(self) -> PMConfig:
        return PMConfig(
            user_id=self.user_id,
            provider=self.pm_provider,
            model=self.pm_model,
            api_key=self.pm_api_key,
            base_url=self.pm_base_url,
            data_dir=self.data_dir,
            vault_dir=self.vault_dir,
            session_id=self.session_id,
        )
