from pathlib import Path

import pytest

from nuvel.memory.resolver import ConfigScopeResolver
from nuvel.memory.scope import Scope

FIXTURE = Path(__file__).parent / "fixtures" / "org_graph.yaml"


def test_known_user_resolves_full_chain_leaf_to_root():
    r = ConfigScopeResolver.from_yaml(FIXTURE)
    chain = r.resolve("albert")
    assert chain.tags() == ["user:albert", "team:platform", "division:eu", "org:acme"]


def test_unknown_user_falls_back_to_user_leaf_only(caplog):
    r = ConfigScopeResolver.from_yaml(FIXTURE)
    with caplog.at_level("WARNING"):
        chain = r.resolve("ghost")
    assert chain.tags() == ["user:ghost"]
    assert any("unknown user" in m.lower() for m in caplog.messages)


def test_org_id_attribute_exposed():
    r = ConfigScopeResolver.from_yaml(FIXTURE)
    assert r.org_id == "acme"
