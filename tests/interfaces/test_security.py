"""interfaces/api/security.py had no direct test until iV's first
self-audit pointed that out (TEST-01) — an omission worth correcting on
its own terms, since this module is the gate in front of every
externally-reachable endpoint."""

import pytest
from fastapi import HTTPException

from interfaces.api.security import (
    check_internal_secret,
    check_secret,
    require_chat_secret,
    resolve_approval_secret,
)


def test_correct_secret_passes(monkeypatch):
    monkeypatch.setenv("API_ACCESS_SECRET", "the-right-value")

    check_secret("the-right-value")


@pytest.mark.parametrize("provided", [None, "", "wrong", "the-right-valu", "the-right-value-plus"])
def test_wrong_or_missing_secret_is_rejected(monkeypatch, provided):
    monkeypatch.setenv("API_ACCESS_SECRET", "the-right-value")

    with pytest.raises(HTTPException) as excinfo:
        check_secret(provided)

    assert excinfo.value.status_code == 403


def test_an_unset_expected_secret_rejects_everything(monkeypatch):
    """Fail closed: a server started without API_ACCESS_SECRET must not
    accept an empty secret as a match for an empty expectation."""
    monkeypatch.delenv("API_ACCESS_SECRET", raising=False)

    for candidate in (None, "", "anything"):
        with pytest.raises(HTTPException):
            check_secret(candidate)


def test_secret_is_read_fresh_on_every_call(monkeypatch):
    """Not cached at import time — a config change takes effect without a
    process restart, and test ordering cannot affect the outcome."""
    monkeypatch.setenv("API_ACCESS_SECRET", "first")
    check_secret("first")

    monkeypatch.setenv("API_ACCESS_SECRET", "second")
    check_secret("second")
    with pytest.raises(HTTPException):
        check_secret("first")


def test_internal_secret_is_a_separate_credential(monkeypatch):
    monkeypatch.setenv("API_ACCESS_SECRET", "api-value")
    monkeypatch.setenv("INTERNAL_TRIGGER_SECRET", "internal-value")

    check_internal_secret("internal-value")
    with pytest.raises(HTTPException):
        check_internal_secret("api-value")


def test_chat_dependency_reads_the_header(monkeypatch):
    monkeypatch.setenv("API_ACCESS_SECRET", "header-value")

    require_chat_secret("header-value")
    with pytest.raises(HTTPException):
        require_chat_secret(None)


def test_resolve_approval_secret_prefers_the_query_and_flags_it(monkeypatch):
    monkeypatch.setenv("API_ACCESS_SECRET", "s3cret-value-long")

    assert resolve_approval_secret("s3cret-value-long", None) == ("s3cret-value-long", True)
    assert resolve_approval_secret(None, "s3cret-value-long") == ("s3cret-value-long", False)


def test_resolve_approval_secret_validates_the_cookie_too(monkeypatch):
    monkeypatch.setenv("API_ACCESS_SECRET", "s3cret-value-long")

    with pytest.raises(HTTPException):
        resolve_approval_secret(None, "forged-cookie-value")
