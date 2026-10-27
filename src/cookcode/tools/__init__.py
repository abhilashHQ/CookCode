from typing import Any

from cookcode.agent.tools import Tool
from cookcode.tools.bash import bash_tool
from cookcode.tools.files import edit_tool, read_tool, write_tool
from cookcode.tools.jobs import job_tools
from cookcode.tools.recall import recall_tool
from cookcode.tools.search import glob_tool, grep_tool


def default_tools(*, recall: bool = False, jobs: bool = False) -> list[Tool[Any]]:
    """recall comes with strategies that mask tool outputs; jobs with the jobs feature."""
    tools: list[Tool[Any]] = [
        read_tool(), write_tool(), edit_tool(), grep_tool(), glob_tool(), bash_tool()
    ]
    if jobs:
        tools += job_tools()
    return [*tools, recall_tool()] if recall else tools
