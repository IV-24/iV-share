"""The redaction filter is a security control, so these tests aim at the
ways a secret actually escapes into a log: through a format argument
rather than the message, through an exception's text, and through
uvicorn's access log line for a URL carrying ?secret=."""

import logging
import sys

import pytest

from core.observability import redaction
from core.observability.logging import JsonFormatter, configure_logging, uvicorn_log_config
from core.observability.redaction import REDACTED, SecretRedactingFilter, redact, register_secret


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    monkeypatch.setattr(redaction, "_registered", set())


def test_redact_replaces_registered_values():
    register_secret("gsk_a_very_secret_key_value")

    assert redact("using gsk_a_very_secret_key_value now") == f"using {REDACTED} now"


def test_short_values_are_ignored():
    """A two-character 'secret' would blank out unrelated text everywhere."""
    register_secret("ab")

    assert redact("about") == "about"


def test_filter_redacts_a_secret_passed_as_a_format_argument():
    register_secret("sk-ant-secret-value-here")
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "auth failed for %s", ("sk-ant-secret-value-here",), None)

    SecretRedactingFilter().filter(record)

    assert "sk-ant-secret-value-here" not in record.getMessage()
    assert REDACTED in record.getMessage()


def test_filter_redacts_exception_text():
    register_secret("gsk_leaky_key_in_traceback")
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", (), None)
    record.exc_text = "Traceback: Authorization: Bearer gsk_leaky_key_in_traceback"

    SecretRedactingFilter().filter(record)

    assert "gsk_leaky_key_in_traceback" not in record.exc_text


def _record_from_live_exception(secret: str, *, chained: bool = False) -> logging.LogRecord:
    """A record carrying exc_info but NOT exc_text -- which is the only
    state that occurs in production. Filters run before any formatter, and
    exc_text is populated lazily by the formatter afterwards, so a test
    that pre-sets exc_text is testing a state the runtime never reaches."""
    try:
        if chained:
            try:
                raise RuntimeError(f"inner {secret}")
            except RuntimeError as inner:
                raise ConnectionError("outer wrapper") from inner
        raise ValueError(f"provider rejected key {secret}")
    except Exception:
        exc_info = sys.exc_info()
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "call failed", (), exc_info)
    assert record.exc_text is None, "precondition: production records reach the filter unformatted"
    return record


def test_filter_redacts_a_secret_inside_a_live_traceback():
    """The leak this closes: an SDK raises an exception whose message
    embeds the credential, and logger.exception formats the traceback."""
    secret = "gsk_live_traceback_key_value"
    register_secret(secret)
    record = _record_from_live_exception(secret)

    SecretRedactingFilter().filter(record)
    rendered = logging.Formatter("%(message)s").format(record)

    assert secret not in rendered
    assert REDACTED in rendered


def test_filter_redacts_a_secret_inside_a_chained_traceback():
    """`raise X from e` prints both tracebacks; adapters/models wraps SDK
    errors exactly that way (ModelUnavailableError(str(exc)) from exc)."""
    secret = "chained-cause-fake-credential-value"
    register_secret(secret)
    record = _record_from_live_exception(secret, chained=True)

    SecretRedactingFilter().filter(record)
    rendered = logging.Formatter("%(message)s").format(record)

    assert secret not in rendered


def test_json_formatter_redacts_a_secret_inside_a_live_traceback():
    """JsonFormatter builds its own exception string, so it has to honour
    the redacted text rather than re-formatting exc_info from scratch."""
    secret = "gsk_json_formatter_leak_value"
    register_secret(secret)
    record = _record_from_live_exception(secret)

    SecretRedactingFilter().filter(record)
    rendered = JsonFormatter().format(record)

    assert secret not in rendered


def test_filter_redacts_an_access_log_line_with_a_query_secret():
    """The concrete case this exists for: /approvals/pending?secret=... is
    a real endpoint, and uvicorn's access log records full request lines."""
    register_secret("the-real-api-access-secret")
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/1.1" %d',
        ("127.0.0.1", "GET", "/approvals/pending?secret=the-real-api-access-secret", 200),
        None,
    )

    SecretRedactingFilter().filter(record)

    assert "the-real-api-access-secret" not in record.getMessage()


def test_register_all_from_env_returns_names_not_values(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_value_from_environment")

    found = redaction.register_all_from_env()

    assert "GROQ_API_KEY" in found
    assert "gsk_value_from_environment" not in found
    assert redact("gsk_value_from_environment") == REDACTED


def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    configure_logging()
    after_first = len([h for h in root.handlers if getattr(h, "_iv_handler", False)])
    configure_logging()
    after_second = len([h for h in root.handlers if getattr(h, "_iv_handler", False)])

    assert after_first == after_second == 1


def test_json_formatter_emits_one_object_per_record():
    import json

    record = logging.LogRecord("iv", logging.INFO, __file__, 1, "hello %s", ("world",), None)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"


def test_uvicorn_log_config_attaches_the_redacting_filter():
    config = uvicorn_log_config()

    assert config["handlers"]["default"]["filters"] == ["redact"]
    assert config["filters"]["redact"]["()"].endswith("SecretRedactingFilter")
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert config["loggers"][logger_name]["handlers"] == ["default"]
