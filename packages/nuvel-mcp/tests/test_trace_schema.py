"""nuvel.skill_run/1 schema and the stdlib validator: content-free by construction."""

import copy
import json
from pathlib import Path

import pytest

from nuvel_mcp import traces
from nuvel_mcp.traces import (
    IDENTIFIER_RE,
    SCHEMA_ID,
    load_schema,
    validate_skill_run,
)

FIXTURE = Path(__file__).parent / "fixtures" / "skill_run_example.json"


@pytest.fixture
def example():
    return json.loads(FIXTURE.read_text())


def deep_get(obj, path):
    for p in path:
        obj = obj[p]
    return obj


def deep_set(obj, path, value):
    deep_get(obj, path[:-1])[path[-1]] = value
    return obj


def test_constants_and_schema_file():
    assert SCHEMA_ID == "nuvel.skill_run/1"
    assert IDENTIFIER_RE.pattern == r"\A[a-z0-9][a-z0-9_.:-]{0,79}\Z"
    schema = load_schema()
    assert schema["properties"]["schema"]["const"] == SCHEMA_ID
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("value", ["radar\n", "radar\nx", "\nradar"])
def test_identifier_re_rejects_newlines_even_with_match(value):
    assert IDENTIFIER_RE.match(value) is None
    assert IDENTIFIER_RE.fullmatch(value) is None
    assert IDENTIFIER_RE.search(value) is None


def test_spec_example_validates(example):
    assert validate_skill_run(example) == []


def test_minimal_trace_validates(example):
    for k in ("steps", "tools", "errors", "routing", "safety", "user_corrected"):
        example.pop(k)
    assert validate_skill_run(example) == []


@pytest.mark.parametrize("bad", ["Ana Garcia", "ana@acme.com", "Radar", "rádar", "x" * 81, "a b", "", "radar\n", "radar٣"])
@pytest.mark.parametrize("path", [("plugin", "name"), ("skill", "name"), ("steps", 0, "id"), ("tools", 0, "name")])
def test_identifier_fields_reject_content(example, bad, path):
    t = deep_set(copy.deepcopy(example), path, bad)
    errors = validate_skill_run(t)
    assert errors
    if bad:  # "" is a substring of everything; the path check below still applies
        assert all(bad not in e for e in errors)
    assert "/" + "/".join(str(p) for p in path) in errors


def test_underscores_allowed(example):
    assert validate_skill_run(deep_set(example, ("tools", 0, "name"), "gmail.search_threads")) == []


@pytest.mark.parametrize("where", [(), ("plugin",), ("skill",), ("steps", 0), ("tools", 0), ("safety",)])
def test_unknown_keys_rejected_everywhere(example, where):
    t = copy.deepcopy(example)
    deep_get(t, where)["note"] = 1
    assert validate_skill_run(t) == ["/" + "/".join(map(str, (*where, "note")))]


def test_unknown_key_in_error_entry_rejected(example):
    example["errors"] = [{"step": "radar.2.email", "class": "auth", "note": 1}]
    assert validate_skill_run(example) == ["/errors/0/note"]


@pytest.mark.parametrize("where", [("steps", 1, "counts"), ("routing",)])
def test_map_keys_must_be_identifiers_and_never_echoed(example, where):
    t = copy.deepcopy(example)
    deep_get(t, where)["Ana Garcia"] = 1
    errors = validate_skill_run(t)
    assert errors and all("Ana" not in e for e in errors)


@pytest.mark.parametrize("where", [("steps", 1, "counts"), ("routing",)])
def test_map_values_must_be_bounded_ints(example, where):
    for v in (-1, 100001, 1.5, True, "3"):
        t = copy.deepcopy(example)
        deep_get(t, where)["read"] = v
        assert validate_skill_run(t), v


def test_client_cannot_send_tenant(example):
    assert "/tenant" in " ".join(validate_skill_run({**example, "tenant": "x"}))


def test_bounds(example):
    cases = [
        (("steps",), [{"id": f"radar.{i}", "status": "ok"} for i in range(51)]),
        (("steps", 1, "counts"), {f"k{i}": 1 for i in range(21)}),
        (("routing",), {f"k{i}": 1 for i in range(21)}),
        (("tools",), [{"name": f"c.t{i}", "calls": 1, "errors": 0} for i in range(51)]),
        (("errors",), [{"step": "radar.1", "class": "other"} for _ in range(21)]),
        (("duration_s",), 86401),
        (("duration_s",), -1),
        (("tools", 0, "calls"), 10001),
        (("tools", 0, "errors"), -1),
        (("steps", 1, "counts", "read"), 100001),
        (("safety", "injection_suspected"), 10001),
    ]
    for path, value in cases:
        t = deep_set(copy.deepcopy(example), path, value)
        assert validate_skill_run(t), path
    # upper bounds are inclusive
    ok = [
        (("steps",), [{"id": f"radar.{i}", "status": "ok"} for i in range(50)]),
        (("steps", 1, "counts"), {f"k{i}": 100000 for i in range(20)}),
        (("duration_s",), 86400),
        (("tools", 0, "calls"), 10000),
    ]
    for path, value in ok:
        assert validate_skill_run(deep_set(copy.deepcopy(example), path, value)) == [], path


@pytest.mark.parametrize("path", [("duration_s",), ("tools", 0, "calls"), ("safety", "injection_suspected")])
def test_booleans_are_not_integers(example, path):
    assert validate_skill_run(deep_set(example, path, True)) == ["/" + "/".join(map(str, path))]


def test_user_corrected_must_be_bool(example):
    assert validate_skill_run({**example, "user_corrected": 0}) == ["/user_corrected"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "nuvel.skill_run/2"),
        ("host", "claude-desktop"),
        ("mode", "batch"),
        ("outcome", "ok"),
        ("run_id", "not-a-uuid"),
        ("run_id", "3f0c2a9e-6b1d-1c8e-9a57-2d4e8f1b0c63"),  # v1, not v4
        ("install_id", "ana@acme.com"),
        ("started_at", "yesterday"),
        ("started_at", "2026-13-40T17:43:00Z"),
        ("started_at", "2026-10-20 17:43"),
    ],
)
def test_enums_consts_and_formats(example, field, value):
    errors = validate_skill_run({**example, field: value})
    assert errors == [f"/{field}"]
    assert all(value not in e for e in errors)


def test_started_at_accepts_any_offset(example):
    for ts in ("2026-10-20T17:43:00+02:00", "2026-10-20T17:43:00.123Z", "2026-10-20T17:43:00-05:30"):
        assert validate_skill_run({**example, "started_at": ts}) == [], ts


@pytest.mark.parametrize("ts", ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:00-05:00"])
def test_started_at_out_of_utc_range_is_rejected(example, ts):
    assert validate_skill_run({**example, "started_at": ts}) == ["/started_at"]


def test_enum_values_for_step_status_and_error_class(example):
    example["steps"][0]["status"] = "error"
    example["errors"] = [{"step": "radar.1.load-state", "class": c} for c in (
        "auth", "rate_limit", "not_found", "permission", "timeout",
        "tool_error", "contract_violation", "registry_missing", "other",
    )][:20]
    assert validate_skill_run(example) == []
    example["errors"][0]["class"] = "Ana Garcia said no"
    assert validate_skill_run(example) == ["/errors/0/class"]


def test_semver(example):
    for bad in ("0.2", "v0.2.0", "0.2.0-beta", "0.2.0\n"):
        assert validate_skill_run(deep_set(copy.deepcopy(example), ("plugin", "version"), bad)) == ["/plugin/version"]


@pytest.mark.parametrize("field", ["schema", "run_id", "install_id", "plugin", "skill", "host", "mode", "started_at", "duration_s", "outcome"])
def test_required_fields(example, field):
    example.pop(field)
    assert validate_skill_run(example) == [f"/{field}"]


def test_safety_requires_both_keys(example):
    example["safety"] = {"injection_suspected": 0}
    assert validate_skill_run(example) == ["/safety/forbidden_tool_requested"]


def test_non_object_inputs():
    for t in (None, [], "nuvel.skill_run/1", 3):
        assert validate_skill_run(t) == ["/"]


def test_wrong_container_types(example):
    example["steps"] = {"id": "radar.1"}
    example["counts"] = 1
    errors = validate_skill_run(example)
    assert "/steps" in errors and "/counts" in errors


def test_unknown_unsafe_keys_are_not_echoed(example):
    errors = validate_skill_run({**example, "Ana Garcia <ana@acme.com>": 1})
    assert errors == ["/*"]


@pytest.mark.parametrize("key", [3, 1.5, None, ("a", "b"), True])
@pytest.mark.parametrize("where", [(), ("plugin",), ("steps", 1, "counts"), ("routing",), ("safety",)])
def test_non_string_keys_reported_as_star(example, where, key):
    t = copy.deepcopy(example)
    deep_get(t, where)[key] = 1
    assert validate_skill_run(t) == ["/" + "/".join(map(str, (*where, "*")))]


def test_check_schema_keywords_rejects_unsupported_keyword_and_format():
    schema = copy.deepcopy(load_schema())
    schema["properties"]["host"]["minLength"] = 1
    with pytest.raises(ValueError, match="minLength"):
        traces.check_schema_keywords(schema)
    schema = copy.deepcopy(load_schema())
    schema["properties"]["run_id"]["format"] = "email"
    with pytest.raises(ValueError, match="format"):
        traces.check_schema_keywords(schema)


@pytest.mark.parametrize("bad_type", ["number", "null", "Integer", ["string", "null"], 3, None])
def test_check_schema_keywords_rejects_unsupported_type(bad_type):
    schema = copy.deepcopy(load_schema())
    schema["properties"]["steps"]["items"]["properties"]["id"]["type"] = bad_type
    with pytest.raises(ValueError, match="type"):
        traces.check_schema_keywords(schema)


def test_packaged_schema_passes_keyword_check():
    traces.check_schema_keywords(load_schema())
