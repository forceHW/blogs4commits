"""All DynamoDB reads and writes for posts. Nothing else in the generator talks to the table."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .config import Config
from .models import DRAFT, CommitEntry, Highlight, Post

# "date" and "status" are DynamoDB reserved words, so every expression uses #placeholders.
_NAMES = {
    "#project": "project",
    "#date": "date",
    "#status": "status",
    "#commits": "commits",
    "#highlights": "highlights",
    "#day_summary": "day_summary",
    "#push_ids": "push_ids",
}


class PostRepository:
    def __init__(self, table_name: str, region: str, endpoint_url: str | None = None):
        kwargs: dict[str, Any] = {"region_name": region}
        if endpoint_url:
            # DynamoDB Local: dummy credentials so real AWS credentials are never used.
            kwargs.update(
                endpoint_url=endpoint_url,
                aws_access_key_id="local",
                aws_secret_access_key="local",
            )
        self._table = boto3.resource("dynamodb", **kwargs).Table(table_name)

    @classmethod
    def from_config(cls, config: Config) -> PostRepository:
        return cls(config.table_name, config.region, config.dynamodb_endpoint)

    # ---------- reads ----------

    def covered_push_ids(self, project: str, from_date: str) -> set[str]:
        """Push IDs already recorded on any post (draft or published) for project, date >= from_date."""
        covered: set[str] = set()
        query: dict[str, Any] = {
            "KeyConditionExpression": Key("project").eq(project) & Key("date").gte(from_date),
            "ProjectionExpression": "#push_ids",
            "ExpressionAttributeNames": {"#push_ids": "push_ids"},
        }
        while True:
            page = self._table.query(**query)
            for item in page.get("Items", []):
                covered |= set(item.get("push_ids", set()))
            if "LastEvaluatedKey" not in page:
                return covered
            query["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def get_post(self, project: str, date: str) -> Post | None:
        resp = self._table.get_item(Key={"project": project, "date": date}, ConsistentRead=True)
        item = resp.get("Item")
        return _from_item(item) if item else None

    # ---------- writes ----------

    def create_draft(self, post: Post) -> bool:
        """Write a new draft. Returns False (and writes nothing) if a post already exists for (project, date)."""
        if not post.push_ids:
            raise ValueError("a post must record at least one push ID")
        try:
            self._table.put_item(
                Item=_to_item(post, status=DRAFT),
                ConditionExpression="attribute_not_exists(#project)",
                ExpressionAttributeNames={"#project": "project"},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def add_commits(
        self,
        project: str,
        date: str,
        new_commits: list[CommitEntry],
        day_summary: str,
        new_highlights: list[Highlight],
        push_ids: set[str],
    ) -> Post:
        """Append late commits to an existing post, replace its day summary, and send it back to draft."""
        if not push_ids:
            raise ValueError("must record at least one push ID")
        resp = self._table.update_item(
            Key={"project": project, "date": date},
            UpdateExpression=(
                "SET #commits = list_append(#commits, :commits), "
                "#highlights = list_append(if_not_exists(#highlights, :empty), :highlights), "
                "#day_summary = :day_summary, "
                "#status = :draft "
                "ADD #push_ids :push_ids"
            ),
            # Only update a post that exists; never create one here.
            ConditionExpression="attribute_exists(#project)",
            ExpressionAttributeNames={k: v for k, v in _NAMES.items() if k != "#date"},
            ExpressionAttributeValues={
                ":commits": [asdict(c) for c in new_commits],
                ":highlights": [asdict(h) for h in new_highlights],
                ":empty": [],
                ":day_summary": day_summary,
                ":draft": DRAFT,
                ":push_ids": set(push_ids),
            },
            ReturnValues="ALL_NEW",
        )
        return _from_item(resp["Attributes"])


def _to_item(post: Post, status: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        "project": post.project,
        "date": post.date,
        "status": status,
        "commits": [asdict(c) for c in post.commits],
        "day_summary": post.day_summary,
        "highlights": [asdict(h) for h in post.highlights],
        "push_ids": set(post.push_ids),  # stored as a DynamoDB string set
    }
    if post.notes is not None:
        item["notes"] = post.notes
    return item


def _from_item(item: dict[str, Any]) -> Post:
    return Post(
        project=item["project"],
        date=item["date"],
        status=item["status"],
        commits=[CommitEntry(**c) for c in item.get("commits", [])],
        day_summary=item.get("day_summary", ""),
        highlights=[Highlight(**h) for h in item.get("highlights", [])],
        push_ids=set(item.get("push_ids", set())),
        notes=item.get("notes"),
    )
