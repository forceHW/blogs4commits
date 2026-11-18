"""Plain data types shared by ingestion, service, and repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

DRAFT = "draft"
PUBLISHED = "published"


@dataclass(frozen=True)
class CommitRecord:
    """One commit found by ingestion, before grouping or summarizing."""

    push_id: str
    project: str  # repo name, e.g. "blogs4commits"
    repo_full_name: str  # owner/repo, e.g. "forceHW/blogs4commits" (agent tools need it)
    sha: str
    message: str
    authored_at: datetime  # timezone-aware
    forced: bool  # came from a force push (history was rewritten)


@dataclass(frozen=True)
class Covered:
    """What earlier posts already include, for skipping repeats."""

    push_ids: set[str]
    shas: set[str]


@dataclass
class CommitEntry:
    sha: str
    message: str
    summary: str  # short AI summary of this commit


@dataclass
class Highlight:
    file: str
    code: str  # verified to appear in the commit's diff
    explanation: str
    sha: str = ""  # commit this highlight comes from


@dataclass
class Post:
    project: str
    date: str  # ISO YYYY-MM-DD, Eastern time
    commits: list[CommitEntry]
    day_summary: str
    highlights: list[Highlight]
    push_ids: set[str]
    status: str = DRAFT
    notes: str | None = None  # learner's own notes, added on the admin page
