"""Static guards against the render-time branches that break hydration.

Same approach as tests/core/test_ports.py: read the real files and assert
on them. The frontend has no test runner, and the failure these cover only
appears on a narrow viewport against a server-rendered page -- so it
survived every check that did exist and showed up on a phone instead.
"""

import re
from pathlib import Path

import pytest

COMPONENTS = Path(__file__).resolve().parents[2] / "frontend" / "components"


def _sources():
    return sorted(COMPONENTS.glob("*.jsx"))


def test_there_are_components_to_check():
    assert _sources(), f"no components found under {COMPONENTS}"


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_no_window_measurement_inside_a_state_initialiser(path):
    """useState(() => ... window ...) renders one value on the server and
    another on a client whose viewport disagrees, which is exactly what
    made the sidebar fail to hydrate on a phone while working on a desktop.
    A measurement belongs in an effect, after mount."""
    source = path.read_text()
    initialisers = re.findall(r"useState\((.*?)\)\s*;", source, flags=re.S)

    offenders = [
        block.strip()[:120]
        for block in initialisers
        if re.search(r"\bwindow\b|\bdocument\b|\bnavigator\b|Date\.now|Math\.random", block)
    ]

    assert not offenders, (
        f"{path.name}: state initialised from a browser measurement or a "
        f"changing value, which the server cannot reproduce: {offenders}"
    )


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_locale_formatted_dates_are_marked_as_intentionally_unstable(path):
    """A date rendered in the viewer's locale genuinely differs between the
    server process and the browser. That is the right behaviour for a
    personal tool, so it is allowed -- but it has to be marked, or it
    reports as a mismatch on every render."""
    source = path.read_text()
    if "toLocale" not in source:
        pytest.skip("no locale-formatted output in this component")

    assert "suppressHydrationWarning" in source, (
        f"{path.name} formats a value in the viewer's locale without "
        "suppressHydrationWarning on the element that holds it"
    )
