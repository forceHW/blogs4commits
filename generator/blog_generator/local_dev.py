"""Helpers for DynamoDB Local. Table shape must match PostsTable in template.yaml."""

from __future__ import annotations

import boto3

DEFAULT_LOCAL_ENDPOINT = "http://localhost:8000"


def local_dynamodb(endpoint_url: str = DEFAULT_LOCAL_ENDPOINT, region: str = "us-east-1"):
    return boto3.resource(
        "dynamodb",
        endpoint_url=endpoint_url,
        region_name=region,
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )


def create_posts_table(dynamodb, table_name: str):
    """Create a table with the same keys and GSI as PostsTable in template.yaml."""
    table = dynamodb.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "project", "AttributeType": "S"},
            {"AttributeName": "date", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "project", "KeyType": "HASH"},
            {"AttributeName": "date", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "status-date-index",
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "date", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )
    table.wait_until_exists()
    return table
