"""What {{agent_name}} advertises on Buzz, derived from what it can do.

A Buzz persona is the public face of the agent in a group: its handle, what
it's for, and the capabilities people can ask it for. Rather than maintaining
that as a second copy of the truth, this module renders it from the agent's
actual configuration — the instruction it runs on and the skills on disk. Add
a skill and the persona says so; there is nothing to keep in sync.

Two surfaces come out of the same :class:`BuzzPersona`:

* :meth:`BuzzPersona.to_profile_content` — the NIP-01 kind-0 metadata event the
  relay worker publishes, so clients show a name and an about blurb;
* :meth:`BuzzPersona.to_intro` — the plain-text answer when someone in the
  group asks the agent what it does.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .agent import AGENT_DESCRIPTION, AGENT_NAME, BuzzConfig
from .skills import discover_skills

# A persona blurb is a chat bio, not a system prompt — keep it short enough to
# read in a client sidebar.
MAX_ABOUT_CHARS = 480
MAX_LISTED_SKILLS = 12


def _first_paragraph(text: str, limit: int = MAX_ABOUT_CHARS) -> str:
    """The lead paragraph of an instruction, trimmed to a bio-sized blurb."""
    paragraph = (text or "").strip().split("\n\n", 1)[0].strip()
    paragraph = " ".join(paragraph.split())
    if len(paragraph) <= limit:
        return paragraph
    return paragraph[: limit - 1].rstrip() + "…"


@dataclass
class BuzzPersona:
    """The agent's public identity in a Buzz group."""

    name: str
    handle: str
    about: str
    skills: list[dict] = field(default_factory=list)
    picture: str = ""

    @classmethod
    def build(cls, config: BuzzConfig | None = None) -> "BuzzPersona":
        config = config or BuzzConfig.from_env()
        return cls(
            name=AGENT_NAME,
            handle=os.getenv("BUZZ_AGENT_HANDLE") or AGENT_NAME,
            about=_first_paragraph(AGENT_DESCRIPTION or config.instruction),
            skills=[
                {"name": s["slug"], "description": s["description"]}
                for s in discover_skills()
            ],
            picture=os.getenv("BUZZ_AGENT_PICTURE", ""),
        )

    # ── rendered surfaces ────────────────────────────────────────────

    def to_profile_content(self) -> str:
        """The JSON body of a NIP-01 kind-0 (`set_metadata`) event."""
        profile: dict[str, object] = {
            "name": self.handle,
            "display_name": self.name,
            "about": self.to_about(),
            "bot": True,
        }
        if self.picture:
            profile["picture"] = self.picture
        return json.dumps(profile, ensure_ascii=False, separators=(",", ":"))

    def to_about(self) -> str:
        """The ``about`` blurb: what it's for, then what it knows."""
        if not self.skills:
            return self.about
        listed = [s["name"] for s in self.skills[:MAX_LISTED_SKILLS]]
        more = len(self.skills) - len(listed)
        suffix = f", +{more} more" if more > 0 else ""
        return f"{self.about}\n\nSkills: {', '.join(listed)}{suffix}"

    def to_intro(self) -> str:
        """A human-readable introduction for the group chat."""
        lines = [f"**{self.name}** (@{self.handle}) — {self.about}"]
        if self.skills:
            lines.append("")
            lines.append("What I can help with:")
            for skill in self.skills[:MAX_LISTED_SKILLS]:
                description = skill["description"] or "(no description)"
                lines.append(f"• `{skill['name']}` — {description}")
            more = len(self.skills) - MAX_LISTED_SKILLS
            if more > 0:
                lines.append(f"• …and {more} more.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "handle": self.handle,
            "about": self.about,
            "picture": self.picture,
            "skills": list(self.skills),
        }


def main() -> None:
    persona = BuzzPersona.build()
    print(persona.to_intro())
    print()
    print("kind-0 metadata:", persona.to_profile_content())


if __name__ == "__main__":
    main()
