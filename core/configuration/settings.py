"""Settings are loaded from the environment and grouped by concern
(secrets, storage, model, permissions) rather than one flat namespace, so
an adapter only ever sees the slice of config it actually needs. Nothing
here hardcodes a key, a path, or an infra choice — an empty environment
produces a valid Settings object with everything unset, and it's up to
the caller (e.g. a CLI/API entrypoint) to decide that's an error for its
use case, not core's job to assume.

StorageConfig deliberately has no provider-specific fields (no
supabase_url, no postgres_dsn, ...) — per the decision to run iV on a
locally-owned SQLite file rather than a hosted database (see
docs/MIGRATION_AUDIT.md), core's config only needs to know the backend
name and a local path. If a hosted-database adapter is ever added back,
its own connection config belongs in that adapter's module, not here.
"""

import os
from dataclasses import dataclass, field

from core.configuration.ports import DEFAULT_API_BASE_URL


@dataclass
class ModelConfig:
    """Provider API keys, present only if configured. None means
    'not configured', not 'invalid' — that distinction is what lets
    ModelRegistry skip an unconfigured provider instead of failing."""

    gemini_api_key: str | None = None
    mistral_api_key: str | None = None
    groq_api_key: str | None = None
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None


@dataclass
class StorageConfig:
    backend: str = "local"  # "local" is the only backend today; see module docstring
    local_db_path: str = "iv.db"


@dataclass
class WorkspaceConfig:
    """Where adapters/workspace's sandboxed git/file/shell tools are
    confined to on disk. No GitHub credential lives here — like
    GMAIL_APP_PASSWORD living in adapters/notifications/email.py's own
    config rather than core's, a GitHub token is an adapter-specific
    credential (see adapters/workspace/config.py), not something core
    needs to know about."""

    workspace_root: str = "workspace"


@dataclass
class SleepCycleConfig:
    """Provider-agnostic sleep-cycle config only — which email service
    sends the notification is an adapter concern (see
    adapters/notifications/email.py's own config), same reasoning as
    StorageConfig staying free of Supabase-specific fields."""

    internal_trigger_secret: str | None = None
    base_url: str = DEFAULT_API_BASE_URL


@dataclass
class Settings:
    model: ModelConfig = field(default_factory=ModelConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    sleep_cycle: SleepCycleConfig = field(default_factory=SleepCycleConfig)
    api_access_secret: str | None = None


def load_settings(env: dict[str, str] | None = None) -> Settings:
    source = env if env is not None else os.environ
    return Settings(
        model=ModelConfig(
            gemini_api_key=source.get("GEMINI_API_KEY"),
            mistral_api_key=source.get("MISTRAL_API_KEY"),
            groq_api_key=source.get("GROQ_API_KEY"),
            anthropic_api_key=source.get("ANTHROPIC_API_KEY"),
            openrouter_api_key=source.get("OPENROUTER_API_KEY"),
        ),
        storage=StorageConfig(
            backend=source.get("IV_STORAGE_BACKEND", "local"),
            local_db_path=source.get("IV_LOCAL_DB_PATH", "iv.db"),
        ),
        workspace=WorkspaceConfig(
            workspace_root=source.get("IV_WORKSPACE_ROOT", "workspace"),
        ),
        sleep_cycle=SleepCycleConfig(
            internal_trigger_secret=source.get("INTERNAL_TRIGGER_SECRET"),
            base_url=source.get("AGENT_IV_BASE_URL", DEFAULT_API_BASE_URL),
        ),
        api_access_secret=source.get("API_ACCESS_SECRET"),
    )
