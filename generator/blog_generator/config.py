"""Loads non-secret settings from config.toml and secrets from environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


@dataclass(frozen=True)
class Config:
    region: str
    table_name: str
    github_username: str
    model: str
    reasoning_effort: str
    timezone: str
    window_days: int
    # From environment (never from the config file):
    openai_api_key: str | None
    github_token: str | None
    dynamodb_endpoint: str | None  # set to use DynamoDB Local instead of AWS


def load_config(path: Path | None = None) -> Config:
    with open(path or DEFAULT_CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)

    return Config(
        region=raw["aws"]["region"],
        table_name=raw["aws"]["table_name"],
        github_username=raw["github"]["username"],
        model=raw["openai"]["model"],
        reasoning_effort=raw["openai"]["reasoning_effort"],
        timezone=raw["schedule"]["timezone"],
        window_days=int(raw["schedule"]["window_days"]),
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        github_token=os.environ.get("GITHUB_TOKEN"),
        dynamodb_endpoint=os.environ.get("DYNAMODB_ENDPOINT") or None,
    )
