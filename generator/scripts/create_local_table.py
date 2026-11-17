"""Create the posts table in DynamoDB Local (run `docker compose up -d` first).

Usage (from generator/):  python -m scripts.create_local_table
"""

from blog_generator.config import load_config
from blog_generator.local_dev import DEFAULT_LOCAL_ENDPOINT, create_posts_table, local_dynamodb


def main() -> None:
    config = load_config()
    dynamodb = local_dynamodb(DEFAULT_LOCAL_ENDPOINT, config.region)
    existing = [t.name for t in dynamodb.tables.all()]
    if config.table_name in existing:
        print(f"Table {config.table_name} already exists locally.")
        return
    create_posts_table(dynamodb, config.table_name)
    print(f"Created {config.table_name} in DynamoDB Local at {DEFAULT_LOCAL_ENDPOINT}.")
    print(f"Use it with:  DYNAMODB_ENDPOINT={DEFAULT_LOCAL_ENDPOINT}")


if __name__ == "__main__":
    main()
