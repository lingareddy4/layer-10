from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from .utils import normalize_space, utc_now_iso


API_ROOT = "https://api.github.com"
USER_AGENT = "layer10-takehome-memory-graph"


@dataclass
class GitHubCorpusConfig:
    owner: str
    repo: str
    max_issues: int = 20
    state: str = "all"
    token: str | None = None


class GitHubCorpusClient:
    def __init__(self, token: str | None = None) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def _get(self, url: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        response = self.session.get(url, params=params, timeout=30)
        if response.status_code == 403 and "rate limit" in response.text.lower():
            reset = response.headers.get("X-RateLimit-Reset")
            if reset:
                sleep_for = max(int(reset) - int(time.time()) + 1, 1)
                time.sleep(min(sleep_for, 30))
                response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def paged_get(
        self, url: str, params: dict[str, Any] | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        page = 1
        results: list[dict[str, Any]] = []
        params = params.copy() if params else {}
        per_page = min(int(params.get("per_page", 100)), 100)
        params["per_page"] = per_page

        while True:
            params["page"] = page
            items = self._get(url, params=params)
            if not items:
                break
            results.extend(items)
            if limit and len(results) >= limit:
                return results[:limit]
            if len(items) < per_page:
                break
            page += 1
        return results


def _event_text(event: dict[str, Any]) -> str:
    event_type = event.get("event", "unknown")
    actor = (event.get("actor") or {}).get("login", "unknown")
    assignee = (event.get("assignee") or {}).get("login")
    label = (event.get("label") or {}).get("name")
    rename_from = (event.get("rename") or {}).get("from")
    rename_to = (event.get("rename") or {}).get("to")

    if event_type in {"closed", "reopened"}:
        return f"Issue was {event_type} by @{actor}."
    if event_type in {"assigned", "unassigned"} and assignee:
        return f"Issue was {event_type} @{assignee} by @{actor}."
    if event_type in {"labeled", "unlabeled"} and label:
        return f"Label '{label}' was {event_type} by @{actor}."
    if event_type == "renamed" and rename_from and rename_to:
        return f"Issue title renamed from '{rename_from}' to '{rename_to}' by @{actor}."
    return f"Event '{event_type}' by @{actor}."


def _issue_to_summary(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": issue["id"],
        "number": issue["number"],
        "title": issue.get("title") or "",
        "state": issue.get("state") or "open",
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "closed_at": issue.get("closed_at"),
        "html_url": issue.get("html_url"),
        "user": (issue.get("user") or {}).get("login"),
        "assignees": [(a or {}).get("login") for a in issue.get("assignees") or [] if a],
        "labels": [
            {"name": (label or {}).get("name"), "color": (label or {}).get("color")}
            for label in issue.get("labels") or []
            if label
        ],
        "body": issue.get("body") or "",
        "comments_count": issue.get("comments") or 0,
    }


def _issue_artifact(repo_full_name: str, issue: dict[str, Any]) -> dict[str, Any]:
    issue_number = issue["number"]
    text = normalize_space(f"{issue.get('title') or ''}\n\n{issue.get('body') or ''}")
    return {
        "artifact_id": f"issue:{issue_number}:body",
        "artifact_type": "issue_body",
        "repo": repo_full_name,
        "issue_number": issue_number,
        "source_id": str(issue["id"]),
        "source_url": issue.get("html_url"),
        "author": (issue.get("user") or {}).get("login"),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "text": text,
        "meta": {
            "state": issue.get("state"),
            "labels": [(label or {}).get("name") for label in issue.get("labels") or []],
            "assignees": [(a or {}).get("login") for a in issue.get("assignees") or []],
        },
    }


def download_github_corpus(config: GitHubCorpusConfig) -> dict[str, Any]:
    client = GitHubCorpusClient(token=config.token)
    issues_url = f"{API_ROOT}/repos/{config.owner}/{config.repo}/issues"

    fetched_issues = client.paged_get(
        issues_url,
        params={
            "state": config.state,
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
        },
        limit=config.max_issues * 2,
    )

    issues_only = [issue for issue in fetched_issues if "pull_request" not in issue]
    issues_only = issues_only[: config.max_issues]

    issue_summaries: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    total_comments = 0
    total_events = 0

    repo_full_name = f"{config.owner}/{config.repo}"

    for issue in issues_only:
        issue_summary = _issue_to_summary(issue)
        issue_summaries.append(issue_summary)
        artifacts.append(_issue_artifact(repo_full_name, issue))

        issue_number = issue["number"]
        comments_url = f"{API_ROOT}/repos/{config.owner}/{config.repo}/issues/{issue_number}/comments"
        events_url = f"{API_ROOT}/repos/{config.owner}/{config.repo}/issues/{issue_number}/events"

        comments = client.paged_get(comments_url, params={"per_page": 100})
        events = client.paged_get(events_url, params={"per_page": 100})
        total_comments += len(comments)
        total_events += len(events)

        for comment in comments:
            comment_id = comment["id"]
            artifacts.append(
                {
                    "artifact_id": f"issue:{issue_number}:comment:{comment_id}",
                    "artifact_type": "comment",
                    "repo": repo_full_name,
                    "issue_number": issue_number,
                    "source_id": str(comment_id),
                    "source_url": comment.get("html_url"),
                    "author": (comment.get("user") or {}).get("login"),
                    "created_at": comment.get("created_at"),
                    "updated_at": comment.get("updated_at"),
                    "text": normalize_space(comment.get("body") or ""),
                    "meta": {},
                }
            )

        for event in events:
            event_id = event.get("id")
            artifacts.append(
                {
                    "artifact_id": f"issue:{issue_number}:event:{event_id}",
                    "artifact_type": "event",
                    "repo": repo_full_name,
                    "issue_number": issue_number,
                    "source_id": str(event_id),
                    "source_url": issue.get("html_url"),
                    "author": (event.get("actor") or {}).get("login"),
                    "created_at": event.get("created_at"),
                    "updated_at": event.get("created_at"),
                    "text": _event_text(event),
                    "meta": {
                        "event": event.get("event"),
                        "assignee": (event.get("assignee") or {}).get("login"),
                        "label": (event.get("label") or {}).get("name"),
                    },
                }
            )

    return {
        "meta": {
            "corpus": "github_issues",
            "owner": config.owner,
            "repo": config.repo,
            "downloaded_at": utc_now_iso(),
            "max_issues_requested": config.max_issues,
            "issues_fetched": len(issue_summaries),
            "comments_fetched": total_comments,
            "events_fetched": total_events,
        },
        "issues": issue_summaries,
        "artifacts": artifacts,
    }

