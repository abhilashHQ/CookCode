import hashlib
import posixpath
from collections.abc import Sequence
from dataclasses import dataclass

from harness.agent.tools import workspace_path
from harness.env.base import Environment

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
MAX_INSTRUCTION_BYTES = 32_000


@dataclass(frozen=True)
class InstructionFile:
    path: str
    text: str
    size: int
    truncated: bool


async def load_instructions(
    env: Environment,
    cwd: str = ".",
    *,
    names: Sequence[str] = INSTRUCTION_FILES,
    max_bytes: int = MAX_INSTRUCTION_BYTES,
) -> list[InstructionFile]:
    """Instruction files from the workspace root, then from cwd when it differs, deduplicated."""
    directories = ["."]
    here = workspace_path(env.root, cwd, ".")
    if here != "." and not posixpath.isabs(here):
        directories.append(here)
    found: list[InstructionFile] = []
    seen: set[str] = set()
    for directory in directories:
        for name in names:
            path = posixpath.normpath(posixpath.join(directory, name))
            try:
                data = await env.read_file(path)
            except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            text = data[:max_bytes].decode("utf-8", "replace")
            found.append(
                InstructionFile(
                    path=path, text=text, size=len(data), truncated=len(data) > max_bytes
                )
            )
    return found


def render_system(base: str, files: Sequence[InstructionFile]) -> str:
    if not files:
        return base
    sections = [base, "# Project instructions"]
    for file in files:
        note = f"\n[truncated: the file has {file.size} bytes]" if file.truncated else ""
        sections.append(f"## {file.path}\n\n{file.text.rstrip()}{note}")
    return "\n\n".join(sections) + "\n"
