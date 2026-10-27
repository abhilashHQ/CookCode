from typing import Any

from harness.agent.tools import Tool
from harness.tools.bash import bash_tool
from harness.tools.files import edit_tool, read_tool, write_tool
from harness.tools.recall import recall_tool
from harness.tools.search import glob_tool, grep_tool


def default_tools(*, recall: bool = False) -> list[Tool[Any]]:
    """recall comes with strategies that mask tool outputs."""
    tools = [read_tool(), write_tool(), edit_tool(), grep_tool(), glob_tool(), bash_tool()]
    return [*tools, recall_tool()] if recall else tools
