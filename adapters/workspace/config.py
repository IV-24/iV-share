"""GitHub credential loading -- kept out of core/configuration/settings.py
for the same reason GMAIL_APP_PASSWORD lives in
adapters/notifications/email.py's own config: which token (if any)
authenticates GitHub API calls is an adapter concern, not something core
needs to know about."""

import os


def load_github_token(env: dict[str, str] | None = None) -> str | None:
    source = env if env is not None else os.environ
    return source.get("GITHUB_TOKEN")
