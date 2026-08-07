"""SampleCache tests: keys, get/put, isolation, corruption tolerance."""
from __future__ import annotations

from pathlib import Path

from nuvel.evalv2.cache import SampleCache
from nuvel.evalv2.schema import EvaluatorResult, ScoredExample


def _example(ex_id: str = "ex-1") -> ScoredExample:
    return ScoredExample(
        id=ex_id,
        input="summarize this",
        score=0.9,
        passed=True,
        evaluator_results=[
            EvaluatorResult(evaluator="llm-judge", name="rubric", score=0.9)
        ],
        cost=0.01,
    )


def test_key_is_deterministic(tmp_path: Path):
    cache = SampleCache(tmp_path)
    k1 = cache.key("summarize", "gpt-4o", "abc123")
    k2 = cache.key("summarize", "gpt-4o", "abc123")
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_get_miss_returns_none(tmp_path: Path):
    cache = SampleCache(tmp_path)
    assert cache.get("summarize", "gpt-4o", "nothing") is None


def test_put_then_get(tmp_path: Path):
    cache = SampleCache(tmp_path)
    example = _example()
    cache.put("summarize", "gpt-4o", "abc123", example)
    got = cache.get("summarize", "gpt-4o", "abc123")
    assert got is not None
    assert got.id == example.id
    assert got.score == example.score
    assert got.evaluator_results[0].name == "rubric"
    # get marks the result as a cache hit
    assert got.cache_hit is True


def test_different_model_is_a_miss(tmp_path: Path):
    cache = SampleCache(tmp_path)
    cache.put("summarize", "gpt-4o", "abc123", _example())
    assert cache.get("summarize", "gpt-4o-mini", "abc123") is None


def test_different_input_is_a_miss(tmp_path: Path):
    cache = SampleCache(tmp_path)
    cache.put("summarize", "gpt-4o", "abc123", _example())
    assert cache.get("summarize", "gpt-4o", "xyz789") is None


def test_corrupt_file_returns_none(tmp_path: Path):
    cache = SampleCache(tmp_path)
    cache.put("summarize", "gpt-4o", "abc123", _example())
    # Corrupt the stored file.
    path = cache._path("summarize", "gpt-4o", "abc123")
    path.write_text("{ garbage not json", encoding="utf-8")
    assert cache.get("summarize", "gpt-4o", "abc123") is None
    # Poisoned entry is dropped.
    assert not path.exists()


def test_clear_wipes_everything(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache = SampleCache(cache_dir)
    cache.put("summarize", "gpt-4o", "abc123", _example())
    cache.put("translate", "gpt-4o", "def456", _example("ex-2"))
    assert cache.get("summarize", "gpt-4o", "abc123") is not None
    cache.clear()
    assert cache.get("summarize", "gpt-4o", "abc123") is None
    assert not cache_dir.exists()


def test_clear_on_empty_cache_is_safe(tmp_path: Path):
    cache = SampleCache(tmp_path / "never-created")
    cache.clear()  # must not raise


def test_atomic_write_leaves_no_temp_files(tmp_path: Path):
    cache = SampleCache(tmp_path)
    cache.put("summarize", "gpt-4o", "abc123", _example())
    skill_dir = tmp_path / "summarize"
    temp_leftovers = list(skill_dir.glob("*.tmp"))
    assert temp_leftovers == []
    # exactly one persisted json file
    json_files = list(skill_dir.glob("*.json"))
    assert len(json_files) == 1
