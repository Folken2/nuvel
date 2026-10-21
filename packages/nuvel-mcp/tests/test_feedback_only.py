"""Hub-optional server, --tools allow-list and ToolInputError conversion."""

import json

import pytest

from nuvel_mcp.cli import main
from nuvel_mcp.server import (
    FEEDBACK_TOOLS,
    HTTP_DEFAULT_TOOLS,
    HUB_TOOLS,
    McpError,
    SkillsMCPServer,
    ToolInputError,
)


def test_no_hub_lists_only_feedback_tools(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    names = {t["name"] for t in s.handle_tools_list({})["tools"]}
    assert names == FEEDBACK_TOOLS == {
        "record_feedback", "check_skill_health", "propose_improvement", "record_run_trace",
    }
    assert s.handle_resources_list({})["resources"] == []


def test_constants():
    assert FEEDBACK_TOOLS == {
        "record_feedback", "check_skill_health", "propose_improvement", "record_run_trace",
    }
    assert "search_skills" in HUB_TOOLS and not (HUB_TOOLS & FEEDBACK_TOOLS)
    assert HTTP_DEFAULT_TOOLS == {
        "search_skills", "list_skills", "list_themes", "get_skill", "fence_value",
        "record_run_trace", "check_skill_health",
    }


def test_tools_allowlist_filters_list_and_call(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path, tools=frozenset({"check_skill_health"}))
    assert [t["name"] for t in s.handle_tools_list({})["tools"]] == ["check_skill_health"]
    with pytest.raises(McpError):
        s.handle_tools_call({"name": "record_feedback", "arguments": {}})


def test_hub_tool_unavailable_without_loader(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    with pytest.raises(McpError):
        s.handle_tools_call({"name": "search_skills", "arguments": {"query": "x"}})


def test_explicit_skills_dir_without_index_still_errors(tmp_path, capsys):
    assert main(["serve", "--skills-dir", str(tmp_path)]) == 1


def test_tools_flag_parsed_to_frozenset():
    import argparse

    from nuvel_mcp.cli import add_serve_arguments

    p = argparse.ArgumentParser()
    add_serve_arguments(p)
    args = p.parse_args(["--tools", "record_feedback, check_skill_health"])
    assert args.tools == frozenset({"record_feedback", "check_skill_health"})
    assert args.skills_dir is None


def test_tool_input_error_becomes_rejected_result(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)

    def boom(args):
        raise ToolInputError(["a is required", "b is bad"])

    s._tools["record_feedback"] = boom
    result = s.handle_tools_call({"name": "record_feedback", "arguments": {}})
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"]) == {
        "status": "rejected",
        "errors": ["a is required", "b is bad"],
    }
