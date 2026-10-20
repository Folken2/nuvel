"""Tests for the meta-agent CachePlugin: reads are dropped once files change."""

import asyncio
import unittest
from types import SimpleNamespace

from nuvel.plugins.cache_plugin import CachePlugin


def _tool(name):
    return SimpleNamespace(name=name)


class TestCacheInvalidation(unittest.TestCase):
    def setUp(self):
        self.plugin = CachePlugin()
        self.ctx = SimpleNamespace(state={})

    def _after(self, name, args, result):
        return asyncio.run(self.plugin.after_tool_callback(
            tool=_tool(name), tool_args=args, tool_context=self.ctx, result=result))

    def _before(self, name, args):
        return asyncio.run(self.plugin.before_tool_callback(
            tool=_tool(name), tool_args=args, tool_context=self.ctx))

    def test_read_is_served_from_cache(self):
        self._after("read_file", {"path": "a.py"}, {"status": "success", "content": "v1"})
        cached = self._before("read_file", {"path": "a.py"})
        self.assertEqual(cached["content"], "v1")

    def test_write_drops_cached_reads(self):
        self._after("read_file", {"path": "a.py"}, {"status": "success", "content": "v1"})
        self._after("validate_agent", {"name": "x"}, {"status": "ok", "errors": []})
        self._after("write_file", {"path": "a.py", "content": "v2"}, {"status": "success"})
        self.assertIsNone(self._before("read_file", {"path": "a.py"}))
        self.assertIsNone(self._before("validate_agent", {"name": "x"}))

    def test_scaffold_drops_cached_reads(self):
        self._after("list_files", {"path": "."}, {"status": "success", "entries": []})
        self._after("scaffold_agent", {"name": "x"}, {"status": "ok"})
        self.assertIsNone(self._before("list_files", {"path": "."}))


if __name__ == "__main__":
    unittest.main()
