import base64
import importlib
import json

import httpx
import pytest

from jarvis import mcp_github


@pytest.fixture
def gh(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t0k")
    mod = importlib.reload(mcp_github)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, dict(req.url.params), req.content))
        assert req.headers["authorization"] == "Bearer t0k"
        p = req.url.path
        if p == "/user":
            return httpx.Response(200, json={"login": "robin"})
        if p == "/search/issues":
            return httpx.Response(200, json={"items": [{
                "number": 7, "title": "Fix login", "state": "open", "user": {"login": "anna"}, "labels": [],
                "comments": 1, "updated_at": "2026-09-29", "html_url": "u", "pull_request": {},
                "repository_url": "https://api.github.com/repos/robin/app"}]})
        if p == "/repos/robin/app/issues/7":
            if req.method == "PATCH":
                return httpx.Response(200, json={**json.loads(req.content), "number": 7, "title": "Fix login",
                                                 "labels": [], "html_url": "u", "repository_url": ".../repos/robin/app"})
            return httpx.Response(200, json={"number": 7, "title": "Fix login", "state": "open", "user": {"login": "anna"},
                                             "labels": [{"name": "bug"}], "comments": 2, "body": "Bitte prüfen",
                                             "pull_request": {}, "html_url": "u", "assignees": [],
                                             "repository_url": "https://api.github.com/repos/robin/app"})
        if p == "/repos/robin/app/issues/7/comments":
            if req.method == "POST":
                return httpx.Response(201, json={"html_url": "c"})
            return httpx.Response(200, json=[{"user": {"login": "x"}, "created_at": "d", "body": f"k{i}"} for i in range(3)])
        if p == "/repos/robin/app/pulls/7":
            return httpx.Response(200, json={"head": {"ref": "fix", "sha": "abc"}, "base": {"ref": "main"},
                                             "draft": False, "merged": False, "mergeable_state": "clean",
                                             "additions": 3, "deletions": 1, "changed_files": 1})
        if p == "/repos/robin/app/commits/abc/check-runs":
            return httpx.Response(200, json={"check_runs": [
                {"name": "tests", "status": "completed", "conclusion": "success"},
                {"name": "lint", "status": "completed", "conclusion": "failure"},
                {"name": "build", "status": "in_progress", "conclusion": None}]})
        if p == "/repos/robin/app/contents/README.md":
            return httpx.Response(200, json={"path": "README.md", "size": 5,
                                             "content": base64.b64encode(b"Hallo").decode()})
        if p == "/repos/robin/secret/issues/1":
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(500)

    mod._transport = httpx.MockTransport(handler)
    mod.client.cache_clear()
    mod.me.cache_clear()
    mod.calls = calls
    yield mod
    importlib.reload(mcp_github)


def test_my_work(gh):
    w = gh.github_my_work()
    assert w["user"] == "robin" and w["review_requests"][0]["repo"] == "robin/app"
    queries = [c[2]["q"] for c in gh.calls if c[1] == "/search/issues"]
    assert "review-requested:robin" in queries[0]


def test_search_replaces_me(gh):
    gh.github_search("is:pr author:@me")
    assert gh.calls[-1][2]["q"] == "is:pr author:robin"


def test_pr_details_with_checks(gh):
    pr = gh.github_issue("robin/app", 7, comments=2)
    assert pr["type"] == "pr" and pr["labels"] == ["bug"] and [c["body"] for c in pr["last_comments"]] == ["k1", "k2"]
    assert pr["checks"]["overall"] == "fehlgeschlagen" and pr["checks"]["failed"] == ["lint (failure)"]
    assert pr["checks"]["pending"] == ["build"]


def test_write_tools(gh):
    assert gh.github_comment("robin/app", 7, "LGTM")["status"] == "kommentiert"
    assert json.loads(gh.calls[-1][3]) == {"body": "LGTM"}
    assert gh.github_set_issue_state("robin/app", 7, "closed", "completed")["state"] == "closed"


def test_read_file_and_errors(gh):
    assert gh.github_read_file("robin/app", "README.md")["content"] == "Hallo"
    with pytest.raises(ValueError, match="Nicht gefunden"):
        gh.github_issue("robin/secret", 1)


async def _tool_names(env: dict) -> list[str]:
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "jarvis.mcp_github"], env={**os.environ, **env})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return [t.name for t in (await s.list_tools()).tools]


async def test_readonly_hides_write_tools():
    normal = await _tool_names({"GITHUB_TOKEN": "x"})
    readonly = await _tool_names({"GITHUB_TOKEN": "x", "GITHUB_READONLY": "true"})
    assert "github_comment" in normal and "github_comment" not in readonly
    assert "github_issue" in readonly
