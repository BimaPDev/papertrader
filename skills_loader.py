"""Loads skills/<name>/SKILL.md reference docs for injection into AI persona
prompts. Strips YAML frontmatter; returns the methodology body only."""

import re
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"
_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def load_skill(name: str) -> str:
    """Read skills/<name>/SKILL.md and strip its YAML frontmatter."""
    text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    return _FRONTMATTER.sub("", text, count=1).strip()


def load_skills(names: list[str]) -> str:
    """Concatenate multiple skills into one labeled reference block."""
    blocks = [f"### Reference: {name}\n\n{load_skill(name)}" for name in names]
    return "\n\n---\n\n".join(blocks)
