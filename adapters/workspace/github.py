"""GitHub REST API access via urllib (stdlib only -- no new dependency).
create_pull_request() is the one workspace capability that talks to
GitHub directly rather than to a local git clone. Requires a token;
without one this raises rather than silently no-opping, since a PR
request with no way to authenticate is a configuration error the caller
should see, not a swallowed failure.
"""

import json
import urllib.error
import urllib.request

_API_BASE = "https://api.github.com"
_TIMEOUT_SECONDS = 30


class GitHubApiError(RuntimeError):
    pass


def create_pull_request(
    *, token: str | None, owner: str, repo: str, title: str, head: str, base: str, body: str = "",
) -> dict:
    if not token:
        raise GitHubApiError("no GitHub token configured (GITHUB_TOKEN)")

    url = f"{_API_BASE}/repos/{owner}/{repo}/pulls"
    payload = json.dumps({"title": title, "head": head, "base": base, "body": body}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "iV-agent",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GitHubApiError(f"GitHub API returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GitHubApiError(f"could not reach GitHub API: {exc.reason}") from exc

    return {"number": data["number"], "url": data["html_url"], "state": data["state"]}
