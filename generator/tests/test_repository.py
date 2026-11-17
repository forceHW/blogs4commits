"""PostRepository against DynamoDB Local (start it with `docker compose up -d`)."""

from __future__ import annotations

import os
import uuid

import pytest

from blog_generator.local_dev import DEFAULT_LOCAL_ENDPOINT, create_posts_table, local_dynamodb
from blog_generator.models import DRAFT, PUBLISHED, CommitEntry, Highlight, Post
from blog_generator.repository import PostRepository

ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT", DEFAULT_LOCAL_ENDPOINT)


@pytest.fixture
def repo():
    dynamodb = local_dynamodb(ENDPOINT)
    name = f"posts-test-{uuid.uuid4().hex[:8]}"
    table = create_posts_table(dynamodb, name)
    try:
        yield PostRepository(name, "us-east-1", ENDPOINT), table
    finally:
        table.delete()


def make_post(project="blogs4commits", date="2026-11-16", push_ids=("p1",), sha="aaa") -> Post:
    return Post(
        project=project,
        date=date,
        commits=[CommitEntry(sha=sha, message="Add table", summary="Added the posts table.")],
        day_summary="Set up storage.",
        highlights=[Highlight(file="template.yaml", code="BillingMode: PAY_PER_REQUEST", explanation="On-demand.")],
        push_ids=set(push_ids),
    )


def test_create_draft_then_get(repo):
    r, _ = repo
    assert r.create_draft(make_post()) is True
    got = r.get_post("blogs4commits", "2026-11-16")
    assert got is not None
    assert got.status == DRAFT
    assert got.push_ids == {"p1"}
    assert got.commits[0].sha == "aaa"


def test_create_draft_twice_skips_second(repo):
    r, _ = repo
    assert r.create_draft(make_post(push_ids=("p1",))) is True
    assert r.create_draft(make_post(push_ids=("p2",))) is False
    # First write is untouched.
    assert r.get_post("blogs4commits", "2026-11-16").push_ids == {"p1"}


def test_create_draft_requires_push_id(repo):
    r, _ = repo
    with pytest.raises(ValueError):
        r.create_draft(make_post(push_ids=()))


def test_add_commits_reverts_published_to_draft_and_keeps_existing(repo):
    r, table = repo
    r.create_draft(make_post())
    # Simulate the admin page publishing it.
    table.update_item(
        Key={"project": "blogs4commits", "date": "2026-11-16"},
        UpdateExpression="SET #s = :p",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":p": PUBLISHED},
    )

    updated = r.add_commits(
        "blogs4commits",
        "2026-11-16",
        new_commits=[CommitEntry(sha="bbb", message="Late fix", summary="Fixed a typo.")],
        day_summary="Set up storage and fixed a typo.",
        new_highlights=[],
        push_ids={"p2", "p1"},  # p1 repeated: string set keeps one copy
    )

    assert updated.status == DRAFT
    assert [c.sha for c in updated.commits] == ["aaa", "bbb"]
    assert updated.day_summary == "Set up storage and fixed a typo."
    assert len(updated.highlights) == 1
    assert updated.push_ids == {"p1", "p2"}


def test_add_commits_never_creates_a_post(repo):
    r, _ = repo
    from botocore.exceptions import ClientError

    with pytest.raises(ClientError):
        r.add_commits("blogs4commits", "2026-01-01", [], "x", [], {"p9"})
    assert r.get_post("blogs4commits", "2026-01-01") is None


def test_covered_push_ids_includes_drafts_and_published_and_respects_from_date(repo):
    r, table = repo
    r.create_draft(make_post(date="2026-11-14", push_ids=("old",)))
    r.create_draft(make_post(date="2026-11-15", push_ids=("p1",)))
    r.create_draft(make_post(date="2026-11-16", push_ids=("p2", "p3")))
    r.create_draft(make_post(project="other-repo", date="2026-11-16", push_ids=("x1",)))
    table.update_item(
        Key={"project": "blogs4commits", "date": "2026-11-15"},
        UpdateExpression="SET #s = :p",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":p": PUBLISHED},
    )

    assert r.covered_push_ids("blogs4commits", "2026-11-15") == {"p1", "p2", "p3"}
