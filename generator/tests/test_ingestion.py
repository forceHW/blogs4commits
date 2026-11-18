"""Ingestion against a fake GitHub (no network)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from blog_generator.ingestion import ZERO_SHA, GitHubClient, compute_window, fetch_commits

TZ = "America/New_York"


# ---------- window ----------


def test_window_is_two_full_eastern_days_before_run():
    now = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)  # 3AM EST on the 17th
    start, end = compute_window(now, TZ, 2)
    assert start == datetime(2026, 11, 15, 5, 0, tzinfo=UTC)  # 15th 00:00 EST
    assert end == datetime(2026, 11, 17, 5, 0, tzinfo=UTC)  # 17th 00:00 EST


def test_window_across_daylight_saving_start():
    # DST began 2026-03-08. Run at 08:00 UTC on the 9th = 4AM EDT.
    now = datetime(2026, 3, 9, 8, 0, tzinfo=UTC)
    start, end = compute_window(now, TZ, 2)
    assert start == datetime(2026, 3, 7, 5, 0, tzinfo=UTC)  # 7th 00:00 EST (UTC-5)
    assert end == datetime(2026, 3, 9, 4, 0, tzinfo=UTC)  # 9th 00:00 EDT (UTC-4)


# ---------- fake GitHub ----------

START, END = compute_window(datetime(2026, 11, 17, 8, 0, tzinfo=UTC), TZ, 2)


def commit(sha, msg="msg", date="2026-11-16T15:00:00Z", parents=1):
    return {
        "sha": sha,
        "commit": {"message": msg, "author": {"date": date}},
        "parents": [{"sha": f"parent{i}"} for i in range(parents)],
    }


def push(push_id, repo, created_at, before, head, ref="refs/heads/main"):
    return {
        "type": "PushEvent",
        "created_at": created_at,
        "repo": {"name": repo},
        "payload": {"push_id": push_id, "ref": ref, "before": before, "head": head},
    }


def fake_github(routes: dict[str, tuple[int, object]], calls: list[str]):
    """routes: 'path' or 'path?k=v&...' -> (status, json). Unknown paths return 500 (fails the test)."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        key_with_query = f"{path}?{request.url.query.decode()}" if request.url.query else path
        for key in (key_with_query, path):
            if key in routes:
                status, body = routes[key]
                return httpx.Response(status, json=body)
        return httpx.Response(500, json={"message": f"unexpected request {key_with_query}"})

    return GitHubClient(transport=httpx.MockTransport(handler))


def records_for(events, extra_routes):
    calls: list[str] = []
    routes = {"/users/forceHW/events/public": (200, events), **extra_routes}
    client = fake_github(routes, calls)
    return fetch_commits(client, "forceHW", START, END), calls


# ---------- cases ----------


def test_normal_push_skips_merge_commits():
    events = [push(1, "forceHW/blogs4commits", "2026-11-16T20:00:00Z", "b1", "h1")]
    recs, _ = records_for(
        events,
        {
            "/repos/forceHW/blogs4commits/compare/b1...h1": (
                200,
                {"status": "ahead", "commits": [commit("c1", "Add table"), commit("m1", "Merge", parents=2)]},
            )
        },
    )
    assert [(r.sha, r.push_id, r.project, r.forced) for r in recs] == [("c1", "1", "blogs4commits", False)]
    assert recs[0].repo_full_name == "forceHW/blogs4commits"
    assert recs[0].message == "Add table"
    assert recs[0].authored_at == datetime(2026, 11, 16, 15, 0, tzinfo=UTC)


def test_new_branch_compares_against_default_branch():
    events = [push(2, "forceHW/other", "2026-11-16T18:00:00Z", ZERO_SHA, "h2", ref="refs/heads/feature")]
    recs, _ = records_for(
        events,
        {
            "/repos/forceHW/other": (200, {"default_branch": "main"}),
            "/repos/forceHW/other/compare/main...h2": (200, {"status": "ahead", "commits": [commit("c2")]}),
        },
    )
    assert [(r.sha, r.project, r.forced) for r in recs] == [("c2", "other", False)]


def test_first_push_of_new_repo_lists_commits_since_window_start():
    events = [push(3, "forceHW/newrepo", "2026-11-16T18:00:00Z", ZERO_SHA, "h3")]
    recs, calls = records_for(
        events,
        {
            "/repos/forceHW/newrepo": (200, {"default_branch": "main"}),
            "/repos/forceHW/newrepo/commits": (200, [commit("c3")]),
        },
    )
    assert [r.sha for r in recs] == ["c3"]
    assert "/repos/forceHW/newrepo/commits" in calls


def test_force_push_diverged_is_marked():
    events = [push(5, "forceHW/blogs4commits", "2026-11-15T11:00:00Z", "b5", "h5")]
    recs, _ = records_for(
        events,
        {"/repos/forceHW/blogs4commits/compare/b5...h5": (200, {"status": "diverged", "commits": [commit("c5")]})},
    )
    assert [(r.sha, r.forced) for r in recs] == [("c5", True)]


def test_compare_404_falls_back_and_marks_forced():
    events = [push(6, "forceHW/blogs4commits", "2026-11-16T10:00:00Z", "gone", "h6")]
    recs, _ = records_for(
        events,
        {
            "/repos/forceHW/blogs4commits/compare/gone...h6": (404, {"message": "Not Found"}),
            "/repos/forceHW/blogs4commits/commits": (200, [commit("c6")]),
        },
    )
    assert [(r.sha, r.forced) for r in recs] == [("c6", True)]


def test_deleted_branch_makes_no_api_calls():
    events = [push(7, "forceHW/blogs4commits", "2026-11-16T10:00:00Z", "b7", ZERO_SHA, ref="refs/heads/old")]
    recs, calls = records_for(events, {})
    assert recs == []
    assert calls == ["/users/forceHW/events/public"]


def test_window_filtering_and_order():
    events = [  # newest first, like GitHub
        push(10, "forceHW/blogs4commits", "2026-11-17T06:00:00Z", "a", "b"),  # after window end -> ignored
        push(11, "forceHW/blogs4commits", "2026-11-16T20:00:00Z", "b11", "h11"),
        {"type": "WatchEvent", "created_at": "2026-11-16T19:00:00Z", "repo": {"name": "x/y"}, "payload": {}},
        push(12, "forceHW/blogs4commits", "2026-11-15T06:00:00Z", "b12", "h12"),
        push(13, "forceHW/blogs4commits", "2026-11-14T12:00:00Z", "c", "d"),  # before window -> stop
    ]
    recs, _ = records_for(
        events,
        {
            "/repos/forceHW/blogs4commits/compare/b11...h11": (200, {"status": "ahead", "commits": [commit("c11")]}),
            "/repos/forceHW/blogs4commits/compare/b12...h12": (200, {"status": "ahead", "commits": [commit("c12")]}),
        },
    )
    assert [r.push_id for r in recs] == ["12", "11"]  # oldest push first


def test_tag_pushes_ignored():
    events = [push(14, "forceHW/blogs4commits", "2026-11-16T10:00:00Z", "a", "b", ref="refs/tags/v1")]
    recs, calls = records_for(events, {})
    assert recs == []
    assert calls == ["/users/forceHW/events/public"]


def test_github_error_is_raised_not_swallowed():
    events = [push(15, "forceHW/blogs4commits", "2026-11-16T10:00:00Z", "a", "b")]
    with pytest.raises(httpx.HTTPStatusError):
        records_for(events, {"/repos/forceHW/blogs4commits/compare/a...b": (502, {"message": "bad gateway"})})
