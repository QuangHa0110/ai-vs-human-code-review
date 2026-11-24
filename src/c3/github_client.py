from collections.abc import Iterator
import math
import time
from urllib.parse import quote

import httpx


class GitHubClient:
    """Small GitHub REST client with bounded rate-limit waits.

    Search raises when a date window exceeds GitHub's 1,000-result limit;
    callers must narrow that window to obtain a complete dataset.
    """

    _API = "https://api.github.com"
    _MAX_RETRIES = 3
    _MAX_WAIT = 60

    def __init__(self, token: str, http: httpx.Client | None = None, sleep=time.sleep):
        self._http = http if http is not None else httpx.Client(timeout=30)
        self._owns_http = http is None
        self._sleep = sleep
        self._headers = {"Accept": "application/vnd.github+json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _get(self, url: str, *, params=None, accept=None) -> httpx.Response:
        target = httpx.URL(url)
        if (target.scheme, target.host, target.port) != ("https", "api.github.com", None) or target.userinfo:
            raise ValueError("GitHub pagination URL has an untrusted origin")
        headers = dict(self._headers)
        if accept is not None:
            headers["Accept"] = accept
        for attempt in range(self._MAX_RETRIES + 1):
            response = self._http.get(url, params=params, headers=headers, follow_redirects=False)
            if response.status_code not in (403, 429):
                response.raise_for_status()
                return response
            try:
                message = response.json().get("message", "").lower()
            except (ValueError, AttributeError):
                message = ""
            limited = (
                response.status_code == 429
                or response.headers.get("X-RateLimit-Remaining") == "0"
                or "Retry-After" in response.headers
                or "rate limit" in message
            )
            if not limited or attempt == self._MAX_RETRIES:
                response.raise_for_status()
            try:
                if "Retry-After" in response.headers:
                    delay = float(response.headers["Retry-After"])
                elif response.headers.get("X-RateLimit-Remaining") == "0" and "X-RateLimit-Reset" in response.headers:
                    delay = max(0, float(response.headers["X-RateLimit-Reset"]) - time.time())
                else:
                    delay = 60 * 2 ** attempt
            except ValueError:
                response.raise_for_status()
            if not math.isfinite(delay) or not 0 <= delay <= self._MAX_WAIT:
                response.raise_for_status()
            self._sleep(delay)
        raise AssertionError("unreachable")

    def _pages(self, url: str, params=None) -> Iterator[httpx.Response]:
        seen = set()
        while url:
            page_key = str(httpx.URL(url, params=params))
            if page_key in seen:
                raise ValueError("GitHub pagination repeated a page")
            seen.add(page_key)
            response = self._get(url, params=params)
            yield response
            url = response.links.get("next", {}).get("url")
            params = None

    def search_merged_prs(self, repo: str, since: str, until: str) -> Iterator[dict]:
        params = {"q": f"repo:{repo} is:pr is:merged created:{since}..{until}", "per_page": 100}
        for response in self._pages(f"{self._API}/search/issues", params):
            payload = response.json()
            if payload.get("total_count", 0) > 1000:
                raise ValueError("GitHub search exceeds 1,000 results; narrow the date window")
            if payload.get("incomplete_results", False):
                raise ValueError("GitHub search returned incomplete results; retry or narrow the date window")
            yield from payload["items"]

    def get_pr(self, repo: str, number: int) -> dict:
        return self._get(f"{self._API}/repos/{repo}/pulls/{number}").json()

    def review_comments(self, repo: str, number: int) -> list[dict]:
        comments = []
        for response in self._pages(f"{self._API}/repos/{repo}/pulls/{number}/comments", {"per_page": 100}):
            comments.extend(response.json())
        return comments

    def first_parent(self, repo: str, sha: str) -> str:
        commit = self._get(f"{self._API}/repos/{repo}/git/commits/{quote(sha, safe='')}").json()
        parents = commit.get("parents") or []
        if not parents or not parents[0].get("sha"):
            raise ValueError(f"Missing first parent for {repo}@{sha}")
        return parents[0]["sha"]

    def merge_base(self, repo: str, base: str, head: str) -> str:
        refs = f"{quote(base, safe='')}...{quote(head, safe='')}"
        comparison = self._get(
            f"{self._API}/repos/{repo}/compare/{refs}", params={"per_page": 1},
        ).json()
        merge_base = (comparison.get("merge_base_commit") or {}).get("sha")
        if not merge_base:
            raise ValueError(f"Missing merge base for {repo} {base}...{head}")
        return merge_base

    def compare_diff(self, repo: str, base: str, head: str) -> str:
        refs = f"{quote(base, safe='')}...{quote(head, safe='')}"
        return self._get(f"{self._API}/repos/{repo}/compare/{refs}", accept="application/vnd.github.diff").text
