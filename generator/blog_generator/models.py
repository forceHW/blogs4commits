"""Plain data types shared by ingestion, service, and repository."""

from __future__ import annotations

from dataclasses import dataclass, field

DRAFT = "draft"
PUBLISHED = "published"


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
