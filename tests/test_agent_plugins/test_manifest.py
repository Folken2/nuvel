"""Tests for plugin.json loading + validation."""

from __future__ import annotations

import json

import pytest

from nuvel.agent_plugins import ManifestError, PluginManifest
from nuvel.agent_plugins.exceptions import SchemaVersionError

from .conftest import FIXTURES, SCHEMA_ID


def _write(root, data):
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
    return root


def test_valid_minimal_loads():
    m = PluginManifest.load(FIXTURES / "valid-minimal")
    assert m.name == "valid-minimal"
    assert m.schema_id == SCHEMA_ID
    assert m.unknown_fields == []


def test_valid_full_loads():
    m = PluginManifest.load(FIXTURES / "valid-full")
    assert m.name == "valid-full"
    assert m.version == "1.2.3"
    assert m.author["email"] == "hello@example.com"
    assert m.keywords == ["example", "demo"]
    assert "com.example.client" in m.extensions
    assert m.unknown_fields == []


def test_missing_schema_raises(tmp_path):
    root = _write(tmp_path / "p", {"name": "p"})
    with pytest.raises(SchemaVersionError):
        PluginManifest.load(root)


def test_missing_name_raises(tmp_path):
    root = _write(tmp_path / "p", {"$schema": SCHEMA_ID})
    with pytest.raises(ManifestError):
        PluginManifest.load(root)


def test_missing_plugin_json_raises(tmp_path):
    with pytest.raises(ManifestError):
        PluginManifest.load(tmp_path / "does-not-exist")


def test_invalid_json_raises(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "plugin.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError):
        PluginManifest.load(root)


def test_unknown_fields_are_reported_not_fatal(tmp_path):
    root = _write(
        tmp_path / "p",
        {"$schema": SCHEMA_ID, "name": "p", "surprise": 1, "another": "x"},
    )
    m = PluginManifest.load(root)
    assert set(m.unknown_fields) == {"surprise", "another"}


def test_bad_type_is_fatal(tmp_path):
    root = _write(tmp_path / "p", {"$schema": SCHEMA_ID, "name": "p", "version": 3})
    with pytest.raises(ManifestError):
        PluginManifest.load(root)


def test_keywords_must_be_string_array(tmp_path):
    root = _write(
        tmp_path / "p", {"$schema": SCHEMA_ID, "name": "p", "keywords": [1, 2]}
    )
    with pytest.raises(ManifestError):
        PluginManifest.load(root)


def test_author_must_be_object(tmp_path):
    root = _write(tmp_path / "p", {"$schema": SCHEMA_ID, "name": "p", "author": "me"})
    with pytest.raises(ManifestError):
        PluginManifest.load(root)


@pytest.mark.parametrize(
    "name",
    ["", "-bad", "bad-", "Bad", "a--b", "a..b", "x" * 65, "with space", "under_score"],
)
def test_invalid_names_rejected(tmp_path, name):
    root = _write(tmp_path / "p", {"$schema": SCHEMA_ID, "name": name})
    with pytest.raises(ManifestError):
        PluginManifest.load(root)


@pytest.mark.parametrize("name", ["a", "my-plugin", "org.example.plugin", "a1b2", "x" * 64])
def test_valid_names_accepted(tmp_path, name):
    root = _write(tmp_path / "p", {"$schema": SCHEMA_ID, "name": name})
    m = PluginManifest.load(root)
    assert m.name == name


def test_non_object_extensions_is_non_fatal(tmp_path):
    root = _write(
        tmp_path / "p", {"$schema": SCHEMA_ID, "name": "p", "extensions": ["x"]}
    )
    m = PluginManifest.load(root)
    assert "extensions" in m.unknown_fields
    assert m.extensions is None


def test_wrong_schema_value_raises(tmp_path):
    root = _write(tmp_path / "p", {"$schema": "https://evil/other.json", "name": "p"})
    with pytest.raises(SchemaVersionError):
        PluginManifest.load(root)
