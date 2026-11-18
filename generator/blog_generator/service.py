"""Service: skip already-posted commits, group by (project, Eastern date), run the agent,
verify highlights, and hand posts to the repository."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable
from zoneinfo import ZoneInfo

from .agent import SummaryRequest, SummaryResult, Summarizer
from .models import CommitEntry, CommitRecord, Highlight, Post
from .verify import check_highlight

# (repo_full_name, sha) -> changed files [{filename, patch?}, ...]
DiffProvider = Callable[[str, str], list[dict]]


@dataclass
class RunReport:
    created: list[str] = field(default_factory=list)  # "project date"
    updated: list[str] = field(default_factory=list)
    previewed: list[Post] = field(default_factory=list)  # dry run only
    skipped_commits: int = 0
    dropped_highlights: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


class Service:
    def __init__(
        self,
        repository,  # PostRepository (or a fake with the same methods)
        summarizer: Summarizer,
        diffs: DiffProvider,
        timezone: str,
        max_retries: int = 2,
        dry_run: bool = False,
        log: Callable[[str], None] = print,
    ):
        self._repo = repository
        self._summarizer = summarizer
        self._diffs = diffs
        self._tz = ZoneInfo(timezone)
        self._max_retries = max_retries
        self._dry_run = dry_run
        self._log = log

    def run(self, records: list[CommitRecord]) -> RunReport:
        report = RunReport()
        groups = self._new_commits_by_group(records, report)
        for (project, date), commits in sorted(groups.items()):
            label = f"{project} {date}"
            try:
                self._process_group(project, date, commits, report)
            except Exception as e:  # one failed group must not stop the others
                self._log(f"FAILED {label}: {type(e).__name__}: {e}")
                report.failures.append(f"{label}: {e}")
        return report

    # ---------- dedupe + grouping ----------

    def _new_commits_by_group(
        self, records: list[CommitRecord], report: RunReport
    ) -> dict[tuple[str, str], list[CommitRecord]]:
        # 1. Same commit seen twice in this run (e.g. pushed to a branch, then merged): keep the first.
        unique: list[CommitRecord] = []
        seen: set[str] = set()
        for r in records:
            if r.sha in seen:
                report.skipped_commits += 1
                continue
            seen.add(r.sha)
            unique.append(r)

        # 2. Group by (project, Eastern author date).
        groups: dict[tuple[str, str], list[CommitRecord]] = defaultdict(list)
        for r in unique:
            groups[(r.project, self._local_date(r))].append(r)

        # 3. Drop commits already on a post (draft or published).
        for project in {p for p, _ in groups}:
            earliest = min(d for p, d in groups if p == project)
            posted = self._repo.covered(project, earliest).shas
            for key in [k for k in groups if k[0] == project]:
                kept = [r for r in groups[key] if r.sha not in posted]
                report.skipped_commits += len(groups[key]) - len(kept)
                if kept:
                    groups[key] = kept
                else:
                    del groups[key]
        return groups

    def _local_date(self, r: CommitRecord) -> str:
        return r.authored_at.astimezone(self._tz).date().isoformat()

    # ---------- one post ----------

    def _process_group(self, project: str, date: str, commits: list[CommitRecord], report: RunReport) -> None:
        existing = self._repo.get_post(project, date)
        req = SummaryRequest(
            project=project,
            repo_full_name=commits[0].repo_full_name,
            date=date,
            commits=commits,
            existing=list(existing.commits) if existing else [],  # copy: don't share the post's list
        )
        result, dropped = self._summarize_verified(req)
        report.dropped_highlights += [f"{project} {date} {sha[:7]}" for sha in dropped]

        by_sha = {c.sha: c for c in result.commits}
        entries = [CommitEntry(sha=r.sha, message=r.message, summary=by_sha[r.sha].summary) for r in commits]
        highlights = [
            Highlight(file=h.file, code=h.code, explanation=h.explanation, sha=r.sha)
            for r in commits
            if (h := by_sha[r.sha].highlight) is not None
        ]
        push_ids = {r.push_id for r in commits}
        label = f"{project} {date}"

        if self._dry_run:
            prior = existing.commits if existing else []
            prior_hl = existing.highlights if existing else []
            report.previewed.append(
                Post(project, date, prior + entries, result.day_summary, prior_hl + highlights,
                     (existing.push_ids if existing else set()) | push_ids)
            )
            self._log(f"DRY RUN {label}: {'would add to existing post' if existing else 'would create draft'}")
            return

        if existing:
            self._repo.add_commits(project, date, entries, result.day_summary, highlights, push_ids)
            report.updated.append(label)
            self._log(f"UPDATED {label} (+{len(entries)} commits, back to draft)")
        else:
            post = Post(project, date, entries, result.day_summary, highlights, push_ids)
            if not self._repo.create_draft(post):
                raise RuntimeError("a post for this date appeared during the run; not overwritten")
            report.created.append(label)
            self._log(f"CREATED {label} ({len(entries)} commits)")

    def _summarize_verified(self, req: SummaryRequest) -> tuple[SummaryResult, list[str]]:
        """Run the agent; retry with feedback on wrong shas or unverifiable highlights.

        After the retries: wrong shas fail the group; bad highlights are dropped (and logged).
        """
        feedback: str | None = None
        for attempt in range(self._max_retries + 1):
            result = self._summarizer.summarize(req, feedback)
            sha_problems = _sha_problems(req, result)
            bad_highlights = {} if sha_problems else self._bad_highlights(req, result)
            if not sha_problems and not bad_highlights:
                return result, []
            feedback = "\n".join(sha_problems + [f"- highlight for {s}: {why}" for s, why in bad_highlights.items()])
            self._log(f"attempt {attempt + 1} for {req.project} {req.date} needs fixes:\n{feedback}")

        if sha_problems:
            raise RuntimeError("agent output did not match the commits: " + "; ".join(sha_problems))
        for c in result.commits:
            if c.sha in bad_highlights:
                c.highlight = None
        return result, list(bad_highlights)

    def _bad_highlights(self, req: SummaryRequest, result: SummaryResult) -> dict[str, str]:
        bad = {}
        for c in result.commits:
            if c.highlight is None:
                continue
            files = self._diffs(req.repo_full_name, c.sha)
            reason = check_highlight(c.highlight.file, c.highlight.code, files)
            if reason:
                bad[c.sha] = reason
        return bad


def _sha_problems(req: SummaryRequest, result: SummaryResult) -> list[str]:
    expected = [r.sha for r in req.commits]
    got = [c.sha for c in result.commits]
    problems = []
    missing = [s for s in expected if s not in got]
    extra = [s for s in got if s not in expected]
    dupes = {s for s in got if got.count(s) > 1}
    if missing:
        problems.append(f"- missing entries for commits: {', '.join(missing)}")
    if extra:
        problems.append(f"- entries for commits that are not new commits in this request: {', '.join(extra)}")
    if dupes:
        problems.append(f"- more than one entry for: {', '.join(sorted(dupes))}")
    return problems
