"""Ingestion: turn the user's public GitHub push events into CommitRecords.

Talks only to GitHub. Skipping already-posted pushes/commits happens in the service.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .models import CommitRecord

GITHUB_API = "https://api.github.com"
ZERO_SHA = "0" * 40
EVENTS_PER_PAGE = 100
MAX_EVENT_PAGES = 3  # GitHub serves at most 300 events


def compute_window(now: datetime, tz_name: str, days: int) -> tuple[datetime, datetime]:
    """The `days` full local days ending at the most recent local midnight, returned in UTC.

    Run at 3AM Eastern on the 17th with days=2 -> [15th 00:00, 17th 00:00) Eastern.
    """
    tz = ZoneInfo(tz_name)
    today = now.astimezone(tz).date()
    end = datetime.combine(today, time(0), tzinfo=tz)
    start = datetime.combine(today - timedelta(days=days), time(0), tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


class GitHubClient:
    def __init__(self, token: str | None = None, transport: httpx.BaseTransport | None = None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "blogs4commits-generator",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(base_url=GITHUB_API, headers=headers, transport=transport, timeout=30)
        self._files_cache: dict[tuple[str, str], list[dict]] = {}

    def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        return self._http.get(path, params=params)

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self.get(path, params)
        resp.raise_for_status()
        return resp.json()

    def commit_files(self, repo_full_name: str, sha: str) -> list[dict]:
        """Changed files of one commit: [{filename, status, patch?}, ...]. Cached per run.

        GitHub omits `patch` for binary or very large files.
        """
        key = (repo_full_name, sha)
        if key not in self._files_cache:
            self._files_cache[key] = self.get_json(f"/repos/{repo_full_name}/commits/{sha}").get("files", [])
        return self._files_cache[key]

    def contents(self, repo_full_name: str, path: str, ref: str) -> Any:
        """File (dict with base64 `content`) or directory listing (list) at a commit."""
        return self.get_json(f"/repos/{repo_full_name}/contents/{path.strip('/')}", {"ref": ref})


def fetch_commits(client: GitHubClient, username: str, start: datetime, end: datetime) -> list[CommitRecord]:
    """All non-merge commits from the user's push events created in [start, end), oldest push first."""
    records: list[CommitRecord] = []
    default_branches: dict[str, str] = {}

    for event in _push_events_in_window(client, username, start, end):
        payload = event["payload"]
        ref: str = payload.get("ref", "")
        if not ref.startswith("refs/heads/"):
            continue  # tag pushes
        branch = ref.removeprefix("refs/heads/")
        before, head = payload["before"], payload["head"]
        if head == ZERO_SHA:
            continue  # branch was deleted; nothing new

        repo_full_name: str = event["repo"]["name"]
        forced = False

        if before == ZERO_SHA:
            # New branch: no previous commit to compare from.
            if repo_full_name not in default_branches:
                default_branches[repo_full_name] = client.get_json(f"/repos/{repo_full_name}")["default_branch"]
            default = default_branches[repo_full_name]
            if branch == default:
                # First push of a new repo: nothing to compare against.
                commits = _commits_since(client, repo_full_name, head, start)
            else:
                # Commits on the new branch that aren't on the default branch.
                commits = client.get_json(f"/repos/{repo_full_name}/compare/{default}...{head}")["commits"]
        else:
            resp = client.get(f"/repos/{repo_full_name}/compare/{before}...{head}")
            if resp.status_code == 404:
                # Old commit no longer exists (force push, then cleaned up).
                commits = _commits_since(client, repo_full_name, head, start)
                forced = True
            else:
                resp.raise_for_status()
                data = resp.json()
                # Normal push: before is an ancestor of head ("ahead"). Anything else = history rewritten.
                forced = data["status"] in ("diverged", "behind")
                commits = data["commits"]

        for c in commits:
            if len(c.get("parents", [])) > 1:
                continue  # merge commit: no new work of its own
            records.append(
                CommitRecord(
                    push_id=str(payload["push_id"]),
                    project=repo_full_name.split("/", 1)[1],
                    repo_full_name=repo_full_name,
                    sha=c["sha"],
                    message=c["commit"]["message"],
                    authored_at=_parse_time(c["commit"]["author"]["date"]),
                    forced=forced,
                )
            )
    return records


def _push_events_in_window(client: GitHubClient, username: str, start: datetime, end: datetime) -> list[dict]:
    """PushEvents created in [start, end), oldest first. GitHub returns events newest first."""
    found: list[dict] = []
    for page in range(1, MAX_EVENT_PAGES + 1):
        events = client.get_json(
            f"/users/{username}/events/public", {"per_page": EVENTS_PER_PAGE, "page": page}
        )
        reached_older = False
        for event in events:
            created = _parse_time(event["created_at"])
            if created >= end:
                continue  # after the window (e.g. pushed between midnight and the run)
            if created < start:
                reached_older = True
                break
            if event.get("type") == "PushEvent":
                found.append(event)
        if reached_older or len(events) < EVENTS_PER_PAGE:
            break
    found.reverse()
    return found


def _commits_since(client: GitHubClient, repo_full_name: str, head: str, since: datetime) -> list[dict]:
    return client.get_json(
        f"/repos/{repo_full_name}/commits",
        {"sha": head, "since": since.isoformat().replace("+00:00", "Z"), "per_page": 100},
    )


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
