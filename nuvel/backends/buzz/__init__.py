"""Buzz backend — env-configured ACP agents with a Nostr identity.

A Buzz agent is a standalone Python package that speaks the Agent Client
Protocol over stdio (so editors and `nuvel doctor` can drive it) and, via
the ``buzz`` overlay, joins a Buzz relay as a Nostr-identified participant.

Unlike the ADK backend there is no FastAPI server and no framework
dependency: configuration is read from the environment (see
``.env.example``) and the model loop talks to an OpenAI-compatible
endpoint over plain HTTP.
"""
