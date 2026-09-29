"""The protected-branch guard is the control that keeps this first
autonomous deployment from writing to main, so its edges get tested
explicitly: configuration overrides, whitespace, and the deliberate
choice to treat an unknown branch as unprotected."""

import pytest

from core.safety.branches import (
    DEFAULT_PROTECTED_BRANCHES,
    ProtectedBranchViolation,
    is_protected_branch,
    protected_branches,
    require_unprotected_branch,
)


def test_defaults_cover_the_usual_names(monkeypatch):
    monkeypatch.delenv("IV_PROTECTED_BRANCHES", raising=False)

    assert protected_branches() == frozenset(DEFAULT_PROTECTED_BRANCHES)
    for name in ("main", "master", "production", "release"):
        assert is_protected_branch(name)


def test_environment_overrides_the_default_list(monkeypatch):
    monkeypatch.setenv("IV_PROTECTED_BRANCHES", "trunk, golden ")

    assert protected_branches() == frozenset({"trunk", "golden"})
    assert is_protected_branch("trunk")
    assert not is_protected_branch("main")


def test_an_empty_override_disables_the_guard(monkeypatch):
    """Explicitly opting out is allowed — the point is that it takes a
    deliberate act by the operator, not a default."""
    monkeypatch.setenv("IV_PROTECTED_BRANCHES", "")

    assert protected_branches() == frozenset()
    assert not is_protected_branch("main")


def test_unknown_branch_is_not_treated_as_protected():
    assert is_protected_branch(None) is False
    assert is_protected_branch("") is False


def test_require_raises_with_actionable_guidance(monkeypatch):
    monkeypatch.delenv("IV_PROTECTED_BRANCHES", raising=False)

    with pytest.raises(ProtectedBranchViolation) as excinfo:
        require_unprotected_branch("main", operation="commit")

    message = str(excinfo.value)
    assert "create_branch" in message
    assert "IV_PROTECTED_BRANCHES" in message


def test_require_passes_on_a_working_branch(monkeypatch):
    monkeypatch.delenv("IV_PROTECTED_BRANCHES", raising=False)

    require_unprotected_branch("iv-work", operation="commit")
