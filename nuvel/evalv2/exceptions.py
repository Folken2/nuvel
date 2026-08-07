"""Error hierarchy for evalv2.

A single base (`EvalError`) lets callers catch everything evalv2 can raise
with one `except`, while the specific subclasses let the runner and CLI
distinguish a bad suite from a bad example from a cache glitch.
"""
from __future__ import annotations


class EvalError(Exception):
    """Base class for every evalv2 error."""


class SuiteError(EvalError):
    """A suite.yaml is missing, unreadable, or structurally invalid."""


class ExampleError(EvalError):
    """An example file is missing required fields or fails to parse."""


class CacheError(EvalError):
    """A cache read or write failed in a way the caller should know about."""


class SchemaVersionError(EvalError):
    """A serialized payload carries a schema version we don't support."""
