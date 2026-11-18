"""OpenAI agent: a tool-use loop that summarizes one (project, date) group of commits."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import CommitEntry, CommitRecord
from .prompts import SYSTEM_PROMPT, render_request


@dataclass
class SummaryRequest:
    project: str
    repo_full_name: str
    date: str
    commits: list[CommitRecord]  # new commits to summarize
    existing: list[CommitEntry] = field(default_factory=list)  # already on the post (context only)


@dataclass
class HighlightOut:
    file: str
    code: str
    explanation: str


@dataclass
class CommitOut:
    sha: str
    summary: str
    highlight: HighlightOut | None


@dataclass
class SummaryResult:
    commits: list[CommitOut]
    day_summary: str


class Summarizer(Protocol):
    def summarize(self, req: SummaryRequest, feedback: str | None = None) -> SummaryResult: ...


class AgentError(RuntimeError):
    pass


_HIGHLIGHT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["file", "code", "explanation"],
    "properties": {
        "file": {"type": "string"},
        "code": {"type": "string"},
        "explanation": {"type": "string"},
    },
}

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["commits", "day_summary"],
    "properties": {
        "commits": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sha", "summary", "highlight"],
                "properties": {
                    "sha": {"type": "string"},
                    "summary": {"type": "string"},
                    "highlight": {"anyOf": [_HIGHLIGHT_SCHEMA, {"type": "null"}]},
                },
            },
        },
        "day_summary": {"type": "string"},
    },
}


def _tool(name: str, description: str, props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        },
        "strict": True,
    }


TOOLS = [
    _tool(
        "get_commit_diff",
        "Get the unified diff of one of the new commits in this request.",
        {"sha": {"type": "string", "description": "Full sha of a new commit from the request."}},
    ),
    _tool(
        "read_file",
        "Read a file from the repository as of the newest commit in this request.",
        {"path": {"type": "string", "description": "Path from the repository root."}},
    ),
    _tool(
        "list_directory",
        "List a directory in the repository as of the newest commit in this request.",
        {"path": {"type": "string", "description": 'Path from the repository root; "" for the root.'}},
    ),
]


class OpenAISummarizer:
    """Runs the Responses API with function tools and a strict JSON output schema."""

    def __init__(
        self,
        client: Any,  # openai.OpenAI
        model: str,
        reasoning_effort: str,
        github: Any,  # GitHubClient
        max_turns: int = 15,
        diff_char_cap: int = 60_000,
        file_char_cap: int = 40_000,
    ):
        self._client = client
        self._model = model
        self._effort = reasoning_effort
        self._github = github
        self._max_turns = max_turns
        self._diff_cap = diff_char_cap
        self._file_cap = file_char_cap

    def summarize(self, req: SummaryRequest, feedback: str | None = None) -> SummaryResult:
        conversation: list[Any] = [{"role": "user", "content": render_request(req, feedback)}]
        for _ in range(self._max_turns):
            resp = self._client.responses.create(
                model=self._model,
                instructions=SYSTEM_PROMPT,
                input=conversation,
                tools=TOOLS,
                reasoning={"effort": self._effort},
                text={"format": {"type": "json_schema", "name": "post", "schema": OUTPUT_SCHEMA, "strict": True}},
            )
            calls = [item for item in resp.output if getattr(item, "type", None) == "function_call"]
            if not calls:
                return _parse_result(resp.output_text)
            conversation += resp.output  # keep reasoning + tool calls in context
            for call in calls:
                conversation.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": self._run_tool(req, call.name, call.arguments),
                    }
                )
        raise AgentError(f"agent did not finish within {self._max_turns} turns")

    # ---------- tools (read-only, scoped to this repo and these commits) ----------

    def _run_tool(self, req: SummaryRequest, name: str, arguments: str) -> str:
        try:
            args = json.loads(arguments or "{}")
            if name == "get_commit_diff":
                return self._get_commit_diff(req, args["sha"])
            ref = req.commits[-1].sha
            if name == "read_file":
                return self._read_file(req.repo_full_name, args["path"], ref)
            if name == "list_directory":
                return self._list_directory(req.repo_full_name, args["path"], ref)
            return f"error: unknown tool {name}"
        except Exception as e:  # tool errors go back to the model, they don't crash the run
            return f"error: {e}"

    def _get_commit_diff(self, req: SummaryRequest, sha: str) -> str:
        allowed = {c.sha for c in req.commits}
        if sha not in allowed:
            return "error: sha is not one of the new commits in this request"
        parts = []
        for f in self._github.commit_files(req.repo_full_name, sha):
            patch = f.get("patch")
            body = patch if patch else "(no text diff: binary or too large)"
            parts.append(f"--- {f.get('filename')} ({f.get('status')})\n{body}")
        return _cap("\n\n".join(parts) or "(no file changes)", self._diff_cap)

    def _read_file(self, repo: str, path: str, ref: str) -> str:
        data = self._github.contents(repo, path, ref)
        if isinstance(data, list):
            return "error: that path is a directory; use list_directory"
        if data.get("encoding") != "base64" or "content" not in data:
            return "error: file content not available (too large or not a regular file)"
        text = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return _cap(text, self._file_cap)

    def _list_directory(self, repo: str, path: str, ref: str) -> str:
        data = self._github.contents(repo, path, ref)
        if not isinstance(data, list):
            return "error: that path is a file; use read_file"
        return "\n".join(f"{e.get('type')}\t{e.get('path')}" for e in data) or "(empty)"


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncated: showing {limit} of {len(text)} characters]"


def _parse_result(output_text: str) -> SummaryResult:
    data = json.loads(output_text)
    commits = []
    for c in data["commits"]:
        h = c.get("highlight")
        commits.append(
            CommitOut(
                sha=c["sha"],
                summary=c["summary"],
                highlight=HighlightOut(h["file"], h["code"], h["explanation"]) if h else None,
            )
        )
    return SummaryResult(commits=commits, day_summary=data["day_summary"])
