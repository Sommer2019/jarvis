"""MCP-Server `github`: Benachrichtigungen, Issues, Pull Requests, CI-Status, Dateien.

Braucht ein GitHub-Token (GITHUB_TOKEN): am besten ein "Fine-grained personal access
token" mit Lesezugriff auf die gewünschten Repos (+ Issues/PRs schreiben, falls Jarvis
Issues anlegen oder kommentieren soll). GITHUB_READONLY=true schaltet alle
schreibenden Tools ab.
"""

from __future__ import annotations

import base64
import logging
import os
from functools import lru_cache

import httpx

from .mcp_common import JarvisMCP

logging.getLogger("httpx").setLevel(logging.WARNING)

API = os.getenv("GITHUB_API_URL", "https://api.github.com")
TOKEN = os.getenv("GITHUB_TOKEN", "")
READONLY = os.getenv("GITHUB_READONLY", "false").lower() in {"1", "true", "yes", "ja"}
MAX_TEXT = 4000

mcp = JarvisMCP("github")
_transport: httpx.BaseTransport | None = None  # für Tests


@lru_cache(maxsize=1)
def client() -> httpx.Client:
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN ist nicht gesetzt (siehe README → GitHub)")
    return httpx.Client(base_url=API, timeout=30, transport=_transport, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "jarvis-assistant",
    })


def gh(method: str, path: str, **kw):
    r = client().request(method, path, **kw)
    if r.status_code == 404:
        raise ValueError(f"Nicht gefunden: {path} (Repo-Name richtig? Token hat Zugriff?)")
    if r.status_code in (401, 403):
        msg = r.json().get("message", "") if r.content else ""
        raise PermissionError(f"GitHub verweigert Zugriff ({r.status_code}): {msg}")
    r.raise_for_status()
    return r.json() if r.content else {}


@lru_cache(maxsize=1)
def me() -> str:
    return gh("GET", "/user")["login"]


def short(text: str | None, n: int = MAX_TEXT) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " […]"


def issue_summary(i: dict) -> dict:
    repo = i.get("repository_url", "").split("/repos/")[-1] or i.get("repository", {}).get("full_name", "")
    return {
        "repo": repo,
        "number": i["number"],
        "title": i["title"],
        "type": "pr" if "pull_request" in i else "issue",
        "state": i["state"],
        "author": (i.get("user") or {}).get("login"),
        "labels": [l["name"] for l in i.get("labels", [])],
        "comments": i.get("comments", 0),
        "updated": i.get("updated_at"),
        "url": i.get("html_url"),
    }


# ------------------------------------------------------------------ lesen
@mcp.tool()
def github_notifications(include_read: bool = False, max_results: int = 30) -> list[dict]:
    """Ungelesene GitHub-Benachrichtigungen (Reviews, Erwähnungen, CI, Issues …)."""
    items = gh("GET", "/notifications", params={"all": str(include_read).lower(), "per_page": max_results})
    return [{
        "id": n["id"],
        "repo": n["repository"]["full_name"],
        "type": n["subject"]["type"],
        "title": n["subject"]["title"],
        "reason": n["reason"],
        "unread": n["unread"],
        "updated": n["updated_at"],
    } for n in items]


@mcp.tool()
def github_my_work() -> dict:
    """Überblick: offene PRs von mir, Review-Anfragen an mich, mir zugewiesene Issues."""
    user = me()

    def search(q: str) -> list[dict]:
        res = gh("GET", "/search/issues", params={"q": q, "per_page": 20, "sort": "updated"})
        return [issue_summary(i) for i in res.get("items", [])]

    return {
        "user": user,
        "review_requests": search(f"is:open is:pr review-requested:{user} archived:false"),
        "my_open_prs": search(f"is:open is:pr author:{user} archived:false"),
        "assigned_issues": search(f"is:open is:issue assignee:{user} archived:false"),
    }


@mcp.tool()
def github_search(query: str, max_results: int = 20) -> list[dict]:
    """Sucht Issues/PRs mit GitHub-Suchsyntax, z.B. 'repo:owner/name is:open label:bug'
    oder 'is:pr is:merged author:@me merged:>2026-09-01'. '@me' wird ersetzt."""
    query = query.replace("@me", me())
    res = gh("GET", "/search/issues", params={"q": query, "per_page": max_results, "sort": "updated"})
    return [issue_summary(i) for i in res.get("items", [])]


@mcp.tool()
def github_list_repos(max_results: int = 30) -> list[dict]:
    """Meine Repositories (zuletzt aktualisiert zuerst)."""
    repos = gh("GET", "/user/repos", params={"sort": "pushed", "per_page": max_results})
    return [{"repo": r["full_name"], "private": r["private"], "description": r.get("description"),
             "open_issues": r["open_issues_count"], "pushed": r["pushed_at"], "default_branch": r["default_branch"]}
            for r in repos]


@mcp.tool()
def github_issue(repo: str, number: int, comments: int = 10) -> dict:
    """Details zu Issue oder PR (repo = 'owner/name') inkl. letzter Kommentare.
    Bei PRs zusätzlich Branches, Merge-Status und CI-Ergebnis."""
    i = gh("GET", f"/repos/{repo}/issues/{number}")
    out = issue_summary(i) | {"body": short(i.get("body")), "assignees": [a["login"] for a in i.get("assignees", [])]}
    if comments and i.get("comments"):
        cs = gh("GET", f"/repos/{repo}/issues/{number}/comments", params={"per_page": 100})[-comments:]
        out["last_comments"] = [{"author": c["user"]["login"], "created": c["created_at"], "body": short(c["body"], 1500)}
                                for c in cs]
    if out["type"] == "pr":
        pr = gh("GET", f"/repos/{repo}/pulls/{number}")
        out |= {"head": pr["head"]["ref"], "base": pr["base"]["ref"], "draft": pr.get("draft"),
                "merged": pr.get("merged"), "mergeable": pr.get("mergeable_state"),
                "additions": pr.get("additions"), "deletions": pr.get("deletions"),
                "changed_files": pr.get("changed_files"),
                "checks": _checks(repo, pr["head"]["sha"])}
    return out


def _checks(repo: str, ref: str) -> dict:
    runs = gh("GET", f"/repos/{repo}/commits/{ref}/check-runs", params={"per_page": 100}).get("check_runs", [])
    summary = {"total": len(runs), "passed": 0, "failed": [], "pending": []}
    for r in runs:
        if r["status"] != "completed":
            summary["pending"].append(r["name"])
        elif r["conclusion"] in ("success", "neutral", "skipped"):
            summary["passed"] += 1
        else:
            summary["failed"].append(f"{r['name']} ({r['conclusion']})")
    summary["overall"] = ("fehlgeschlagen" if summary["failed"] else "läuft" if summary["pending"]
                          else "grün" if runs else "keine Checks")
    return summary


@mcp.tool()
def github_ci_status(repo: str, ref: str = "") -> dict:
    """CI-Status eines Branches/Commits (ohne ref: Standard-Branch) + letzte Workflow-Läufe."""
    if not ref:
        ref = gh("GET", f"/repos/{repo}")["default_branch"]
    runs = gh("GET", f"/repos/{repo}/actions/runs", params={"per_page": 5, "branch": ref}).get("workflow_runs", [])
    return {"ref": ref, "checks": _checks(repo, ref), "recent_runs": [
        {"workflow": r["name"], "status": r["status"], "conclusion": r["conclusion"],
         "commit": (r.get("head_commit") or {}).get("message", "").split("\n")[0], "url": r["html_url"]}
        for r in runs]}


@mcp.tool()
def github_commits(repo: str, branch: str = "", max_results: int = 10) -> list[dict]:
    """Letzte Commits eines Repos/Branches."""
    params = {"per_page": max_results} | ({"sha": branch} if branch else {})
    return [{"sha": c["sha"][:7], "message": c["commit"]["message"].split("\n")[0],
             "author": c["commit"]["author"]["name"], "date": c["commit"]["author"]["date"]}
            for c in gh("GET", f"/repos/{repo}/commits", params=params)]


@mcp.tool()
def github_read_file(repo: str, path: str, ref: str = "") -> dict:
    """Liest eine Datei aus einem Repo (z.B. README.md). Große Dateien werden gekürzt."""
    data = gh("GET", f"/repos/{repo}/contents/{path}", params={"ref": ref} if ref else None)
    if isinstance(data, list):
        return {"type": "dir", "entries": [f"{e['name']}{'/' if e['type'] == 'dir' else ''}" for e in data]}
    text = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    return {"type": "file", "path": data["path"], "size": data["size"], "content": short(text, 20000)}


# ---------------------------------------------------------------- schreiben
@mcp.tool(enabled=not READONLY)
def github_create_issue(repo: str, title: str, body: str = "", labels: list[str] | None = None) -> dict:
    """Legt ein Issue an. Vorher Titel/Text kurz bestätigen lassen."""
    i = gh("POST", f"/repos/{repo}/issues", json={"title": title, "body": body, "labels": labels or []})
    return issue_summary(i)


@mcp.tool(enabled=not READONLY)
def github_comment(repo: str, number: int, body: str) -> dict:
    """Kommentiert ein Issue oder einen PR. Nur nach Bestätigung des Wortlauts."""
    c = gh("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})
    return {"url": c["html_url"], "status": "kommentiert"}


@mcp.tool(enabled=not READONLY)
def github_set_issue_state(repo: str, number: int, state: str, reason: str = "") -> dict:
    """Schließt (state='closed', reason='completed'|'not_planned') oder öffnet (state='open') ein Issue/PR.
    Nur nach ausdrücklicher Bestätigung."""
    body = {"state": state} | ({"state_reason": reason} if reason else {})
    return issue_summary(gh("PATCH", f"/repos/{repo}/issues/{number}", json=body))


@mcp.tool(enabled=not READONLY)
def github_mark_notifications_read(thread_id: str = "") -> dict:
    """Markiert eine Benachrichtigung (thread_id) oder alle als gelesen."""
    if thread_id:
        gh("PATCH", f"/notifications/threads/{thread_id}")
    else:
        gh("PUT", "/notifications", json={"read": True})
    return {"status": "gelesen"}


if __name__ == "__main__":
    mcp.run()
