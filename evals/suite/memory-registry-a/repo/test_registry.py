import re
from pathlib import Path

from registry import COMMANDS

DOCS = (Path(__file__).parent / "pyproject.toml").read_text()


def test_every_command_is_documented():
    documented = set(re.findall(r'^"([a-z_]+)" = ', DOCS.split("[tool.cli.commands]")[1], re.M))
    assert documented == set(COMMANDS)
