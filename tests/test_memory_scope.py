from nuvel.memory.scope import Scope, ScopeChain


def test_scope_tag_is_level_colon_id():
    assert Scope(level="team", id="platform").tag() == "team:platform"


def test_scope_chain_tags_preserves_leaf_to_root_order():
    chain = ScopeChain(
        scopes=[
            Scope(level="user", id="albert"),
            Scope(level="team", id="platform"),
            Scope(level="org", id="acme"),
        ]
    )
    assert chain.tags() == ["user:albert", "team:platform", "org:acme"]


def test_scope_chain_contains_uses_value_equality():
    chain = ScopeChain(scopes=[Scope(level="team", id="platform")])
    assert chain.contains(Scope(level="team", id="platform"))
    assert not chain.contains(Scope(level="team", id="other"))


def test_scope_is_hashable_for_set_membership():
    s = Scope(level="user", id="a")
    assert s in {Scope(level="user", id="a")}
