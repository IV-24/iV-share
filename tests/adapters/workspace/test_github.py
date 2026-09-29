"""The one workspace primitive that talks to a real external service --
create_pull_request()'s urllib call is mocked here; everything else in
adapters/workspace is exercised against real git/filesystem operations
elsewhere in this test package."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from adapters.workspace.github import GitHubApiError, create_pull_request


def test_create_pull_request_without_token_raises():
    with pytest.raises(GitHubApiError, match="no GitHub token"):
        create_pull_request(
            token=None, owner="iv-24", repo="agent-iv", title="t", head="feature", base="main"
        )


def test_create_pull_request_success_returns_number_url_state():
    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps(
        {"number": 42, "html_url": "https://github.com/iv-24/agent-iv/pull/42", "state": "open"}
    ).encode("utf-8")
    fake_response.__enter__.return_value = fake_response

    with patch("urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
        result = create_pull_request(
            token="ghp_fake", owner="iv-24", repo="agent-iv", title="Add feature",
            head="feature-branch", base="main", body="details",
        )

    assert result == {"number": 42, "url": "https://github.com/iv-24/agent-iv/pull/42", "state": "open"}
    request = mock_urlopen.call_args.args[0]
    assert request.full_url == "https://api.github.com/repos/iv-24/agent-iv/pulls"
    assert request.get_header("Authorization") == "Bearer ghp_fake"
    sent_payload = json.loads(request.data.decode("utf-8"))
    assert sent_payload == {"title": "Add feature", "head": "feature-branch", "base": "main", "body": "details"}


def test_create_pull_request_http_error_is_wrapped():
    error = urllib.error.HTTPError(
        url="https://api.github.com/repos/iv-24/agent-iv/pulls", code=422,
        msg="Unprocessable", hdrs=None, fp=None,
    )
    error.read = lambda: b'{"message": "Validation Failed"}'

    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(GitHubApiError, match="422"):
            create_pull_request(
                token="ghp_fake", owner="iv-24", repo="agent-iv", title="t", head="feature", base="main"
            )


def test_create_pull_request_network_error_is_wrapped():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no network")):
        with pytest.raises(GitHubApiError, match="could not reach GitHub API"):
            create_pull_request(
                token="ghp_fake", owner="iv-24", repo="agent-iv", title="t", head="feature", base="main"
            )
