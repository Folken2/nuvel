"""LocalRecipeStore, load_recipe_store and the --recipe-store flag."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from nuvel_mcp.cli import main
from nuvel_mcp.recipes import LocalRecipeStore, RecipeStore, load_recipe_store
from nuvel_mcp.server import SkillsMCPServer


def _signal(tenant=None, signal_id=None, at="2026-10-27T23:59:30+00:00"):
    s = {"signal_id": signal_id or str(uuid.uuid4()), "at": at}
    if tenant is not None:
        s["tenant"] = tenant
    return s


def _recipe(tenant=None, version=1, recipe_id=None):
    r = {"recipe_id": recipe_id or str(uuid.uuid4()), "version": version, "title": "t"}
    if tenant is not None:
        r["tenant"] = tenant
    return r


def test_signal_dedup_same_tenant(tmp_path):
    store = LocalRecipeStore(tmp_path)
    sig = _signal("acme")
    assert store.append_signal(sig) is True
    assert store.append_signal(dict(sig)) is False
    # A fresh instance (another process) sees the same line.
    assert LocalRecipeStore(tmp_path).append_signal(dict(sig)) is False
    files = list((tmp_path / "_recipes" / "signals").glob("*.jsonl"))
    assert [f.name for f in files] == ["2026-10-27.jsonl"]
    assert len(files[0].read_text().splitlines()) == 1


def test_signal_same_id_other_tenant_kept(tmp_path):
    store = LocalRecipeStore(tmp_path)
    sid = str(uuid.uuid4())
    assert store.append_signal(_signal("a", sid))
    assert store.append_signal(_signal("b", sid))
    assert store.append_signal(_signal(None, sid))
    (f,) = (tmp_path / "_recipes" / "signals").glob("*.jsonl")
    assert len(f.read_text().splitlines()) == 3


def test_signal_day_is_utc_of_at(tmp_path):
    store = LocalRecipeStore(tmp_path)
    store.append_signal(_signal(at="2026-10-27T23:30:00-05:00"))
    assert (tmp_path / "_recipes" / "signals" / "2026-10-28.jsonl").is_file()


def test_signal_concurrent_dedup(tmp_path):
    sig = _signal("acme")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: LocalRecipeStore(tmp_path).append_signal(dict(sig)), range(16)))
    assert results.count(True) == 1


def test_recipe_version_written_once(tmp_path):
    store = LocalRecipeStore(tmp_path)
    r = _recipe("acme")
    assert store.put_recipe(r) is True
    assert store.put_recipe({**r, "title": "changed"}) is False
    assert store.put_recipe({**r, "version": 2}) is True
    (path,) = [p for p in (tmp_path / "_recipes" / "recipes").rglob("v1.json")]
    assert json.loads(path.read_text())["title"] == "t"
    assert not list((tmp_path / "_recipes").rglob(".tmp-*"))


def test_recipe_paths_hide_tenant(tmp_path):
    store = LocalRecipeStore(tmp_path)
    store.put_recipe(_recipe("acme-corp"))
    store.append_signal(_signal("acme-corp"))
    paths = [str(p) for p in (tmp_path / "_recipes").rglob("*")]
    assert paths and not any("acme" in p for p in paths)
    # Same recipe_id under another tenant is a separate recipe.
    rid = str(uuid.uuid4())
    assert store.put_recipe(_recipe("a", recipe_id=rid))
    assert store.put_recipe(_recipe("b", recipe_id=rid))
    assert store.put_recipe(_recipe(None, recipe_id=rid))


def test_recipe_rejects_unsafe_id(tmp_path):
    with pytest.raises(ValueError):
        LocalRecipeStore(tmp_path).put_recipe(_recipe(recipe_id="../x"))


def test_store_satisfies_protocol(tmp_path):
    assert isinstance(LocalRecipeStore(tmp_path), RecipeStore)


def test_load_recipe_store_local(tmp_path):
    assert isinstance(load_recipe_store("local", tmp_path), LocalRecipeStore)


@pytest.mark.parametrize("spec", ["", "nope", "mod:", ":f", "no_such_mod_xyz:f", "os:no_such_attr"])
def test_load_recipe_store_rejects_bad_spec(spec, tmp_path):
    with pytest.raises(ValueError):
        load_recipe_store(spec, tmp_path)


def test_server_recipe_store_accessor(tmp_path):
    assert isinstance(SkillsMCPServer(None, feedback_dir=tmp_path)._recipe_store(), LocalRecipeStore)
    custom = object()
    assert SkillsMCPServer(None, feedback_dir=tmp_path, recipe_store=custom)._recipe_store() is custom


def test_cli_recipe_store_flag(capsys):
    with pytest.raises(SystemExit) as e:
        main(["serve", "--help"])
    assert e.value.code == 0 and "--recipe-store" in capsys.readouterr().out


def test_cli_bad_recipe_store_exits_1(tmp_path, capsys):
    assert main(["serve", "--feedback-dir", str(tmp_path), "--recipe-store", "no_such_mod_xyz:f"]) == 1
    assert "recipe store" in capsys.readouterr().err.lower()
