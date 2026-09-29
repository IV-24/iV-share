"""Secret redaction for anything iV writes out — logs, error strings, API
responses.

The threat this exists for is mundane and real: a provider SDK raises an
exception whose message embeds the request it tried to send (headers
included), or a config-dump endpoint grows a field nobody thought about,
and an API key ends up in `.launchd/backend.log` or on a phone screen.
Rather than auditing every call site, secrets are registered once at
startup (register_all_from_env) and every logged record is scrubbed of
their *values* on the way out.

Registration is by value, not by name: what gets replaced is the literal
secret string wherever it appears, so a key leaking through a stack trace
inside a third-party library is caught just as well as one iV logs
itself. Values shorter than _MIN_LENGTH are ignored — redacting a
two-character "secret" would blank out unrelated text everywhere and make
logs useless.
"""

from __future__ import annotations

import logging
import os

REDACTED = "***REDACTED***"

# Env var names whose *values* must never appear in output. Extend when a
# new credential is introduced; the names themselves are not secret.
SECRET_ENV_VARS = [
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "API_ACCESS_SECRET",
    "INTERNAL_TRIGGER_SECRET",
    "GITHUB_TOKEN",
    "GMAIL_APP_PASSWORD",
    "SUPABASE_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
]

_MIN_LENGTH = 8
_registered: set[str] = set()


def register_secret(value: str | None) -> None:
    if value and len(value) >= _MIN_LENGTH:
        _registered.add(value)


def register_all_from_env(env: dict[str, str] | None = None) -> list[str]:
    """Registers every configured SECRET_ENV_VARS value. Returns the
    *names* that were found set — safe to log, unlike the values."""
    source = env if env is not None else os.environ
    found = []
    for name in SECRET_ENV_VARS:
        value = source.get(name)
        if value:
            register_secret(value)
            found.append(name)
    return found


def secret_values() -> frozenset[str]:
    return frozenset(_registered)


def clear_registered_secrets() -> None:
    """Test hook only — production code registers once at startup."""
    _registered.clear()


def redact(text: str) -> str:
    """Replaces every registered secret value in text. Longest first, so
    a secret that contains another registered value can't be partially
    replaced into an unrecognizable fragment."""
    if not text:
        return text
    for value in sorted(_registered, key=len, reverse=True):
        if value in text:
            text = text.replace(value, REDACTED)
    return text


class SecretRedactingFilter(logging.Filter):
    """Scrubs registered secret values from a record's message and args
    before any handler formats it.

    Rendering the message here (record.getMessage()) and collapsing args
    is deliberate: a secret can live in an arg rather than the format
    string, and a handler-level formatter would otherwise re-expand the
    original, unredacted args. Returns True always — this filter edits
    records, it never drops them.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        if not _registered:
            return True
        try:
            rendered = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not break logging
            return True
        cleaned = redact(rendered)
        if cleaned != rendered or record.args:
            record.msg = cleaned
            record.args = ()

        # Format the traceback here rather than waiting for a handler's
        # formatter to do it. exc_text is populated lazily, *after* filters
        # run, so redacting only an already-populated exc_text scrubbed a
        # field that is None every time this matters -- and the traceback
        # then reached the log unredacted. That is the module docstring's
        # own threat ("a provider SDK raises an exception whose message
        # embeds the request it tried to send"), so it has to be the case
        # that works. Formatting it now also means logging.Formatter reuses
        # this redacted value instead of re-deriving one from exc_info.
        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            except Exception:  # noqa: BLE001 - a bad traceback must not break logging
                record.exc_text = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
