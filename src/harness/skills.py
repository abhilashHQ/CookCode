"""Skills: instructions the model loads only when a task needs them.

A skill is a folder with a SKILL.md whose frontmatter names and describes it, in the
repository's `.harness/skills/` or the user's skills directory (the repository wins on a name
clash). Only the names and descriptions go into the system prompt, fixed for the session; the
`skill` tool returns a skill's body and lists the other files in its folder. What that saves is
measured as the body tokens a session did not load against inlining every skill."""

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from harness.agent.tools import Tool, ToolContext, ToolOutput
from harness.core.cancel import CancelToken
from harness.env.base import Environment

REPO_SKILLS = ".harness/skills"
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

DESCRIPTION = (
    "Load a skill by name: instructions for a kind of task, listed in the system prompt. Load "
    "one before starting a task it describes."
)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    files: tuple[str, ...]
    source: str


def parse_skill(text: str, source: str, files: tuple[str, ...] = ()) -> Skill | None:
    """None when SKILL.md lacks frontmatter with a valid name and a description."""
    match = FRONTMATTER.match(text)
    if match is None:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip().strip("\"'")
    name, description = fields.get("name", ""), fields.get("description", "")
    if not NAME.match(name) or not description:
        return None
    return Skill(name, description, match.group(2).strip(), files, source)


async def _repo_skills(env: Environment) -> list[Skill]:
    listing = await env.exec(
        f"cd {shlex.quote(REPO_SKILLS)} 2>/dev/null && find . -type f | sort", cwd=".",
        timeout_s=30, max_output_bytes=100_000, cancel=CancelToken(),
    )
    paths = [line.removeprefix("./") for line in listing.output.splitlines() if line.strip()]
    skills: list[Skill] = []
    for path in paths:
        folder, _, leaf = path.rpartition("/")
        if leaf != "SKILL.md" or "/" in folder or not folder:
            continue
        others = tuple(p for p in paths if p.startswith(folder + "/") and p != path)
        text = (await env.read_file(f"{REPO_SKILLS}/{path}")).decode(errors="replace")
        skill = parse_skill(text, f"{REPO_SKILLS}/{folder}", others)
        if skill is not None:
            skills.append(skill)
    return skills


def _user_skills(root: Path) -> list[Skill]:
    skills: list[Skill] = []
    for file in sorted(root.glob("*/SKILL.md")):
        others = tuple(str(p.relative_to(root)) for p in sorted(file.parent.rglob("*"))
                       if p.is_file() and p != file)
        skill = parse_skill(file.read_text(errors="replace"), str(file.parent), others)
        if skill is not None:
            skills.append(skill)
    return skills


async def load_skills(env: Environment, user_root: Path) -> list[Skill]:
    by_name = {skill.name: skill for skill in _user_skills(user_root)}
    by_name.update({skill.name: skill for skill in await _repo_skills(env)})
    return sorted(by_name.values(), key=lambda skill: skill.name)


def skills_profile(skills: list[Skill]) -> str:
    if not skills:
        return ""
    listed = "\n".join(f"- {skill.name}: {skill.description}" for skill in skills)
    return f"Skills you can load with the skill tool:\n{listed}"


class SkillArgs(BaseModel):
    name: str = Field(description="The skill's name, as listed in the system prompt.")


def skill_tool(skills: list[Skill]) -> Tool[SkillArgs]:
    by_name = {skill.name: skill for skill in skills}

    async def load(args: SkillArgs, ctx: ToolContext) -> ToolOutput:
        skill = by_name.get(args.name)
        if skill is None:
            known = ", ".join(by_name) or "none"
            return ToolOutput(content=f"no skill {args.name!r}. Skills: {known}",
                              status="error", exit_status="error")
        files = "".join(f"\n- {skill.source}/{f.split('/', 1)[-1]}" for f in skill.files)
        extra = f"\n\nOther files in this skill (read them if the body points to them):{files}"
        return ToolOutput(content=skill.body + (extra if files else ""))

    return Tool(name="skill", description=DESCRIPTION, args_model=SkillArgs,
                side_effect="read_only", handler=load)
