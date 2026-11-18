"""Service with a fake model and an in-memory repository (repository itself is tested against DynamoDB Local)."""

from __future__ import annotations

from datetime import UTC, datetime

from blog_generator.agent import CommitOut, HighlightOut, SummaryResult
from blog_generator.models import DRAFT, PUBLISHED, CommitEntry, CommitRecord, Covered, Post
from blog_generator.service import Service

REPO = "forceHW/blogs4commits"
PATCH = "@@ -0,0 +1,2 @@\n+def add(a, b):\n+    return a + b\n"
GOOD = HighlightOut("calc.py", "def add(a, b):\n    return a + b", "Adds numbers.")
BAD = HighlightOut("calc.py", "def add(a, b): return a+b", "Made up.")


def rec(sha, when="2026-11-16T15:00:00+00:00", push="p1", project="blogs4commits", forced=False):
    return CommitRecord(push, project, f"forceHW/{project}", sha, f"msg {sha}", datetime.fromisoformat(when), forced)


class FakeRepo:
    def __init__(self, posts=()):
        self.posts = {(p.project, p.date): p for p in posts}
        self.created, self.added = [], []

    def covered(self, project, from_date):
        shas = {c.sha for (p, d), post in self.posts.items() if p == project and d >= from_date for c in post.commits}
        return Covered(push_ids=set(), shas=shas)

    def get_post(self, project, date):
        return self.posts.get((project, date))

    def create_draft(self, post):
        if (post.project, post.date) in self.posts:
            return False
        self.posts[(post.project, post.date)] = post
        self.created.append(post)
        return True

    def add_commits(self, project, date, new_commits, day_summary, new_highlights, push_ids):
        post = self.posts[(project, date)]
        post.commits += new_commits
        post.highlights += new_highlights
        post.day_summary, post.status = day_summary, DRAFT
        post.push_ids |= push_ids
        self.added.append((project, date, [c.sha for c in new_commits]))


class FakeModel:
    """Returns scripted results in order; records each request and feedback."""

    def __init__(self, *results, fail_for=()):
        self.results = list(results)
        self.calls = []
        self.fail_for = set(fail_for)

    def summarize(self, req, feedback=None):
        self.calls.append((req, feedback))
        if req.project in self.fail_for:
            raise RuntimeError("model unavailable")
        if self.results:
            return self.results.pop(0)
        # Default: one valid entry per commit.
        return SummaryResult([CommitOut(r.sha, f"did {r.sha}", GOOD) for r in req.commits], "day")


def service(repo, model, **kw):
    return Service(repo, model, diffs=lambda repo_name, sha: [{"filename": "calc.py", "patch": PATCH}],
                   timezone="America/New_York", log=lambda m: None, **kw)


def test_creates_one_draft_per_project_and_eastern_date():
    repo, model = FakeRepo(), FakeModel()
    report = service(repo, model).run([
        rec("a", "2026-11-16T15:00:00+00:00"),
        rec("b", "2026-11-16T03:00:00+00:00"),  # 10PM Eastern on the 15th
        rec("c", "2026-11-16T16:00:00+00:00", project="other"),
    ])
    assert sorted(report.created) == ["blogs4commits 2026-11-15", "blogs4commits 2026-11-16", "other 2026-11-16"]
    post = repo.posts[("blogs4commits", "2026-11-16")]
    assert post.status == DRAFT and [c.sha for c in post.commits] == ["a"]
    assert post.highlights[0].sha == "a" and post.push_ids == {"p1"}
    assert report.failures == []


def test_repeated_sha_in_one_run_is_summarized_once():
    repo, model = FakeRepo(), FakeModel()
    report = service(repo, model).run([rec("a", push="p1"), rec("a", push="p2"), rec("b", push="p2")])
    assert [r.sha for r in model.calls[0][0].commits] == ["a", "b"]
    assert report.skipped_commits == 1


def test_already_posted_shas_are_skipped_and_fully_posted_day_makes_no_model_call():
    existing = Post("blogs4commits", "2026-11-16", [CommitEntry("a", "m", "s")], "d", [], {"p0"}, PUBLISHED)
    repo, model = FakeRepo([existing]), FakeModel()
    report = service(repo, model).run([rec("a", push="p9")])
    assert model.calls == [] and report.skipped_commits == 1 and repo.added == []


def test_late_commit_is_added_to_existing_post_with_context():
    existing = Post("blogs4commits", "2026-11-16", [CommitEntry("a", "m", "old summary")], "d", [], {"p0"}, PUBLISHED)
    repo, model = FakeRepo([existing]), FakeModel()
    report = service(repo, model).run([rec("a"), rec("b", push="p2")])
    req, _ = model.calls[0]
    assert [r.sha for r in req.commits] == ["b"]
    assert [c.summary for c in req.existing] == ["old summary"]
    assert report.updated == ["blogs4commits 2026-11-16"]
    post = repo.posts[("blogs4commits", "2026-11-16")]
    assert post.status == DRAFT and [c.sha for c in post.commits] == ["a", "b"]
    assert post.commits[0].summary == "old summary"  # reviewed summary untouched


def test_bad_highlight_is_retried_with_feedback_then_accepted():
    model = FakeModel(
        SummaryResult([CommitOut("a", "s", BAD)], "day"),
        SummaryResult([CommitOut("a", "s", GOOD)], "day"),
    )
    repo = FakeRepo()
    report = service(repo, model).run([rec("a")])
    assert len(model.calls) == 2
    assert model.calls[0][1] is None and "highlight for a" in model.calls[1][1]
    assert repo.created[0].highlights[0].code == GOOD.code
    assert report.dropped_highlights == []


def test_highlight_still_bad_after_retries_is_dropped_but_post_saved():
    bad = lambda: SummaryResult([CommitOut("a", "s", BAD)], "day")
    model, repo = FakeModel(bad(), bad(), bad()), FakeRepo()
    report = service(repo, model).run([rec("a")])
    assert len(model.calls) == 3  # first try + 2 retries
    assert repo.created[0].highlights == []
    assert report.dropped_highlights == ["blogs4commits 2026-11-16 a"]


def test_wrong_shas_after_retries_fail_that_group_only():
    wrong = lambda: SummaryResult([CommitOut("zzz", "s", None)], "day")
    model = FakeModel(wrong(), wrong(), wrong())  # first group (alphabetical) gets these
    repo = FakeRepo()
    report = service(repo, model).run([rec("a", project="aaa"), rec("b", project="bbb")])
    assert len(report.failures) == 1 and report.failures[0].startswith("aaa 2026-11-16")
    assert report.created == ["bbb 2026-11-16"]


def test_model_exception_is_recorded_and_others_continue():
    model, repo = FakeModel(fail_for={"aaa"}), FakeRepo()
    report = service(repo, model).run([rec("a", project="aaa"), rec("b", project="bbb")])
    assert report.failures and report.created == ["bbb 2026-11-16"]


def test_dry_run_writes_nothing_and_previews_post():
    repo, model = FakeRepo(), FakeModel()
    report = service(repo, model, dry_run=True).run([rec("a")])
    assert repo.created == [] and report.created == []
    assert report.previewed[0].commits[0].summary == "did a"


def test_force_pushed_flag_reaches_the_model():
    repo, model = FakeRepo(), FakeModel()
    service(repo, model).run([rec("a", forced=True)])
    assert model.calls[0][0].commits[0].forced is True
