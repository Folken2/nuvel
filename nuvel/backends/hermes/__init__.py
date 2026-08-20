"""Hermes backend — profile-shaped agents for a Hermes install.

A Hermes agent isn't a Python package: it is a *profile* directory that the
Hermes runtime loads. Three files carry everything:

    SOUL.md       identity — who the agent is, what it is for, how it speaks
    config.yaml   model, turn budget, platform wiring, enabled skills
    skills/       Anthropic-format SKILL.md directories, loaded on demand

There is no server, no model loop, and no dependency list here — Hermes owns
the runtime. Scaffolding a Hermes agent means writing those files and then
dropping the directory into ``<hermes_home>/profiles/<name>/``, which is the
same layout :mod:`nuvel.bots` manages via the ``hermes`` CLI.

The ``hermes-gateway`` overlay (``--with-telegram``) swaps in a config.yaml
that turns the Telegram platform on and adds its DM policy knobs.
"""
