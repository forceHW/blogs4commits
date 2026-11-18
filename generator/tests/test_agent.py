"""OpenAISummarizer loop with a fake OpenAI client and fake GitHub (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from blog_generator.agent import AgentError, OpenAISummarizer, SummaryRequest
from blog_generator.models import CommitRecord
from blog_generator.prompts import render_request

REC = CommitRecord("p1", "blogs4commits", "forceHW/blogs4commits", "abc123", "Add calc",
                   datetime(2026, 11, 16, 15, tzinfo=UTC), forced=True)
REQ = SummaryRequest("blogs4commits", "forceHW/blogs4commits", "2026-11-16", [REC])
FINAL = {"commits": [{"sha": "abc123", "summary": "Added calc.",
                      "highlight": {"file": "calc.py", "code": "x", "explanation": "e"}}],
         "day_summary": "Calc day."}


def call(name, args, call_id="c1"):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


def response(output=(), text=""):
    return SimpleNamespace(output=list(output), output_text=text)


class FakeOpenAI:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []
        self.responses = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "input": list(kwargs["input"])})
        return self._responses.pop(0)


class FakeGitHub:
    def commit_files(self, repo, sha):
        return [{"filename": "calc.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+x"}]

    def contents(self, repo, path, ref):
        return [{"type": "file", "path": "calc.py"}]


def test_tool_call_then_final_answer():
    client = FakeOpenAI(
        response([call("get_commit_diff", {"sha": "abc123"})]),
        response(text=json.dumps(FINAL)),
    )
    result = OpenAISummarizer(client, "m", "medium", FakeGitHub()).summarize(REQ)

    assert result.day_summary == "Calc day."
    assert result.commits[0].highlight.file == "calc.py"
    second = client.requests[1]
    tool_output = second["input"][-1]
    assert tool_output["type"] == "function_call_output" and "+x" in tool_output["output"]
    assert second["reasoning"] == {"effort": "medium"}
    assert second["text"]["format"]["strict"] is True


def test_diff_tool_refuses_other_shas():
    client = FakeOpenAI(
        response([call("get_commit_diff", {"sha": "not-in-request"})]),
        response(text=json.dumps(FINAL)),
    )
    OpenAISummarizer(client, "m", "medium", FakeGitHub()).summarize(REQ)
    assert "not one of the new commits" in client.requests[1]["input"][-1]["output"]


def test_turn_cap():
    client = FakeOpenAI(*[response([call("list_directory", {"path": ""})]) for _ in range(3)])
    with pytest.raises(AgentError):
        OpenAISummarizer(client, "m", "medium", FakeGitHub(), max_turns=3).summarize(REQ)


def test_large_diff_is_truncated():
    class BigGitHub(FakeGitHub):
        def commit_files(self, repo, sha):
            return [{"filename": "big.py", "status": "added", "patch": "+" + "y" * 500}]

    client = FakeOpenAI(response([call("get_commit_diff", {"sha": "abc123"})]), response(text=json.dumps(FINAL)))
    OpenAISummarizer(client, "m", "medium", BigGitHub(), diff_char_cap=100).summarize(REQ)
    assert "[truncated" in client.requests[1]["input"][-1]["output"]


def test_prompt_marks_force_push_and_includes_feedback():
    text = render_request(REQ, feedback="- highlight for abc123: wrong")
    assert "[force-pushed]" in text and "highlight for abc123" in text
