"""Nightly entry point.

    python -m blog_generator.main             # generate and write drafts
    python -m blog_generator.main --dry-run   # generate and print, write nothing

Exit code 1 if any (project, date) failed, so the GitHub Actions run shows as failed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from .agent import OpenAISummarizer
from .config import load_config
from .ingestion import GitHubClient, compute_window, fetch_commits
from .repository import PostRepository
from .service import RunReport, Service

MODEL_PLACEHOLDER = "REPLACE_WITH_MODEL_ID"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate blog drafts from recent commits.")
    parser.add_argument("--dry-run", action="store_true", help="print posts instead of writing them")
    args = parser.parse_args(argv)

    config = load_config()
    if config.model == MODEL_PLACEHOLDER:
        print("error: set [openai].model in generator/config.toml", file=sys.stderr)
        return 2
    if not config.openai_api_key:
        print("error: OPENAI_API_KEY is not set", file=sys.stderr)
        return 2

    from openai import OpenAI  # imported here so tests don't need an API key

    github = GitHubClient(config.github_token)
    start, end = compute_window(datetime.now(UTC), config.timezone, config.window_days)
    print(f"Window: {start.isoformat()} .. {end.isoformat()} (UTC)")

    records = fetch_commits(github, config.github_username, start, end)
    print(f"Found {len(records)} commits in push events.")

    service = Service(
        repository=PostRepository.from_config(config),
        summarizer=OpenAISummarizer(OpenAI(api_key=config.openai_api_key), config.model,
                                    config.reasoning_effort, github),
        diffs=github.commit_files,
        timezone=config.timezone,
        dry_run=args.dry_run,
    )
    report = service.run(records)
    _print_report(report)
    return 1 if report.failures else 0


def _print_report(report: RunReport) -> None:
    print("\n===== Report =====")
    print(f"Created: {report.created or 'none'}")
    print(f"Updated: {report.updated or 'none'}")
    print(f"Skipped (already posted or repeated): {report.skipped_commits}")
    if report.dropped_highlights:
        print(f"Highlights dropped after retries: {report.dropped_highlights}")
    for post in report.previewed:
        print(f"\n--- {post.project} {post.date} ---")
        for c in post.commits:
            print(f"* {c.sha[:7]} {c.message.splitlines()[0]}\n  {c.summary}")
        print(f"\nDay summary:\n{post.day_summary}")
        for h in post.highlights:
            print(f"\n[{h.sha[:7]}] {h.file}: {h.explanation}\n{h.code}")
    if report.failures:
        print(f"\nFAILURES: {report.failures}")


if __name__ == "__main__":
    sys.exit(main())
