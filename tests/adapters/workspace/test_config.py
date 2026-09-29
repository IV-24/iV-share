"""adapters/workspace/config.py exists to keep a GitHub credential out of
core/configuration — small enough that it went untested until iV's own
audit noticed (TEST-01)."""

from adapters.workspace.config import load_github_token


def test_returns_the_token_when_set():
    assert load_github_token({"GITHUB_TOKEN": "ghp_example_value"}) == "ghp_example_value"


def test_returns_none_when_unset():
    assert load_github_token({}) is None


def test_reads_the_real_environment_by_default(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_environment")

    assert load_github_token() == "ghp_from_environment"


def test_core_settings_do_not_carry_the_github_token(monkeypatch):
    """The separation this module exists for: a GitHub credential is an
    adapter concern and must not appear in core's Settings."""
    from core.configuration.settings import load_settings

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_environment")
    settings = load_settings()

    assert "ghp_from_environment" not in repr(settings)
