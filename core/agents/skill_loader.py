"""Minimal skill loader — scans a skills directory and builds prompt context."""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("hiveflows")

SKILLS_ROOT = os.environ.get("SKILLS_PATH", "/skills")


@dataclass
class Skill:
    name: str
    path: Path
    description: str
    scripts: list[str] = field(default_factory=list)


def load_skills(skills_dir: str | None = None, filter_names: list[str] | None = None) -> list[Skill]:
    """Scan the skills directory and return loaded skills.

    Args:
        skills_dir: Optional override for the skills root directory.
        filter_names: If None, load all skills. If empty list [], load none.
                      If a list of names, load only those skills.
    """
    if filter_names is not None and len(filter_names) == 0:
        return []

    root = Path(skills_dir or SKILLS_ROOT)
    if not root.is_dir():
        logger.warning(f"Skills directory not found: {root}")
        return []

    skills = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if filter_names is not None and entry.name not in filter_names:
            logger.debug(f"Skipping {entry.name}: not in filter_names")
            continue
        skill_md = entry / "skill.md"
        if not skill_md.exists():
            logger.debug(f"Skipping {entry.name}: no skill.md")
            continue

        description = skill_md.read_text(encoding="utf-8")

        # Collect available scripts
        scripts = []
        scripts_dir = entry / "scripts"
        if scripts_dir.is_dir():
            for script in sorted(scripts_dir.iterdir()):
                if script.is_file() and script.suffix in (".py", ".sh"):
                    scripts.append(str(script))

        skill = Skill(
            name=entry.name,
            path=entry,
            description=description,
            scripts=scripts,
        )
        skills.append(skill)
        logger.info(f"Loaded skill: {skill.name} ({len(scripts)} scripts)")

    return skills


def build_skills_prompt(skills: list[Skill]) -> str:
    """Build a prompt section describing all available skills."""
    if not skills:
        return ""

    parts = ["\n\n---\n## Skills disponibles\n"]
    for skill in skills:
        parts.append(f"### Skill : {skill.name}\n")
        parts.append(skill.description.strip())
        if skill.scripts:
            parts.append("\n**Chemins des scripts (utilisables avec run_script) :**")
            for s in skill.scripts:
                parts.append(f"- `{s}`")
        parts.append("")

    return "\n".join(parts)
