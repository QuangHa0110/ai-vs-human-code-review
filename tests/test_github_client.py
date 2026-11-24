import httpx
import pytest

from c3.github_client import GitHubClient


def client(handler, sleep=lambda _: None):
    return GitHubClient("test-token", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleep)


def test_search_follows_pages_and_sends_auth_and_created_bounds():
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        assert "repo:org/repo is:pr is:merged created:2024-01-01..2024-02-01" == request.url.params["q"]
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json={"total_count": 2, "items": [{"number": 2}]})
        return httpx.Response(200, json={"total_count": 2, "items": [{"number": 1}]}, headers={"Link": '<https://api.github.com/search/issues?q=repo%3Aorg%2Frepo%20is%3Apr%20is%3Amerged%20created%3A2024-01-01..2024-02-01&page=2>; rel="next"'})

    assert list(client(handle).search_merged_prs("org/repo", "2024-01-01", "2024-02-01")) == [{"number": 1}, {"number": 2}]
    assert len(requests) == 2


def test_search_rejects_result_cap_and_incomplete_results():
    for payload in ({"total_count": 1001, "items": []}, {"total_count": 1, "incomplete_results": True, "items": []}):
        with pytest.raises(ValueError, match="narrow|incomplete"):
            list(client(lambda _: httpx.Response(200, json=payload)).search_merged_prs("org/repo", "2024-01-01", "2024-02-01"))


def test_get_pr_and_compare_diff_media_type():
    def handle(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.url.path == "/repos/org/repo/pulls/7":
            return httpx.Response(200, json={"number": 7})
        assert request.url.path == "/repos/org/repo/compare/base...head"
        assert request.headers["Accept"] == "application/vnd.github.diff"
        return httpx.Response(200, text="diff --git a/a b/a\n")

    github = client(handle)
    assert github.get_pr("org/repo", 7) == {"number": 7}
    assert github.compare_diff("org/repo", "base", "head") == "diff --git a/a b/a\n"


def test_comments_follow_list_pagination():
    def handle(request):
        assert request.url.path == "/repos/org/repo/pulls/7/comments"
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"id": 2}])
        return httpx.Response(200, json=[{"id": 1}], headers={"Link": '<https://api.github.com/repos/org/repo/pulls/7/comments?page=2>; rel="next"'})

    assert client(handle).review_comments("org/repo", 7) == [{"id": 1}, {"id": 2}]


@pytest.mark.parametrize("url", ["https://evil.example/comments", "http://api.github.com/comments", "https://api.github.com:444/comments"])
def test_rejects_untrusted_next_link_without_sending_token(url):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[], headers={"Link": f'<{url}>; rel="next"'})

    with pytest.raises(ValueError, match="origin"):
        client(handle).review_comments("org/repo", 7)
    assert len(requests) == 1


def test_rate_limit_waits_until_reset_then_retries(monkeypatch):
    monkeypatch.setattr("c3.github_client.time.time", lambda: 1000)
    sleeps = []
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1004"})
        return httpx.Response(200, json={"number": 7})

    assert client(handle, sleeps.append).get_pr("org/repo", 7) == {"number": 7}
    assert sleeps == [4]


@pytest.mark.parametrize("status,headers,payload,expected_sleeps", [
    (429, {"Retry-After": "2", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "5000"}, {}, [2, 2, 2]),
    (403, {}, {"message": "You have exceeded a secondary rate limit."}, [60]),
])
def test_transient_limits_have_bounded_retries(status, headers, payload, expected_sleeps):
    sleeps = []
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, json=payload)

    with pytest.raises(httpx.HTTPStatusError):
        client(handle, sleeps.append).get_pr("org/repo", 7)
    assert len(requests) == len(expected_sleeps) + 1
    assert sleeps == expected_sleeps


@pytest.mark.parametrize("headers", [{}, {"X-RateLimit-Remaining": "100", "X-RateLimit-Reset": "1004"}])
def test_secondary_limit_waits_a_minute_without_using_primary_reset(headers, monkeypatch):
    monkeypatch.setattr("c3.github_client.time.time", lambda: 1000)
    sleeps = []
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(403, headers=headers, json={"message": "You have exceeded a secondary rate limit."})
        return httpx.Response(200, json={"number": 7})

    assert client(handle, sleeps.append).get_pr("org/repo", 7) == {"number": 7}
    assert sleeps == [60]


def test_long_rate_limit_wait_fails_without_sleep(monkeypatch):
    monkeypatch.setattr("c3.github_client.time.time", lambda: 1000)
    sleeps = []
    with pytest.raises(httpx.HTTPStatusError):
        client(lambda _: httpx.Response(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "5000"}), sleeps.append).get_pr("org/repo", 7)
    assert sleeps == []


@pytest.mark.parametrize("status", [403, 404, 500])
def test_other_errors_raise_without_retry(status):
    sleeps = []
    with pytest.raises(httpx.HTTPStatusError):
        client(lambda _: httpx.Response(status, json={"message": "Forbidden"}), sleeps.append).get_pr("org/repo", 7)
    assert sleeps == []
