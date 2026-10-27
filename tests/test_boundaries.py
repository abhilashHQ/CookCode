import ast
from pathlib import Path

import pytest

CORE = Path(__file__).parents[1] / "src" / "cookcode" / "core"
FORBIDDEN_MODULES = {"subprocess", "socket", "http", "urllib", "sys", "httpx", "requests"}
FORBIDDEN_CALLS = {"print", "input", "open"}
ALLOWED_COOKCODE_IMPORTS = {"cookcode.core"}


def _core_files() -> list[Path]:
    return sorted(CORE.rglob("*.py"))


def _imported_modules(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_core_exists() -> None:
    assert _core_files()


def test_core_imports_no_io_modules() -> None:
    for path in _core_files():
        for module in _imported_modules(ast.parse(path.read_text())):
            assert module.split(".")[0] not in FORBIDDEN_MODULES, f"{path.name} imports {module}"


def test_core_imports_only_core_from_harness() -> None:
    for path in _core_files():
        for module in _imported_modules(ast.parse(path.read_text())):
            if module.split(".")[0] == "cookcode":
                assert any(
                    module == allowed or module.startswith(allowed + ".")
                    for allowed in ALLOWED_COOKCODE_IMPORTS
                ), f"{path.name} imports {module}"


def test_core_never_calls_print_input_open_or_reads_environ() -> None:
    for path in _core_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_CALLS, f"{path.name} calls {node.func.id}"
            if isinstance(node, ast.Attribute):
                assert node.attr != "environ", f"{path.name} reads os.environ"


AGENT = Path(__file__).parents[1] / "src" / "cookcode" / "agent"
TOOLS = Path(__file__).parents[1] / "src" / "cookcode" / "tools"
AGENT_ALLOWED_HARNESS = (
    "cookcode.core",
    "cookcode.agent",
    "cookcode.store.base",
    "cookcode.providers.base",
    "cookcode.env.base",
)
TOOLS_ALLOWED_HARNESS = (
    "cookcode.core",
    "cookcode.agent.tools",
    "cookcode.agent.hooks",
    "cookcode.env.base",
    "cookcode.tools",
)


def _import_violation(module: str, allowed_harness: tuple[str, ...]) -> bool:
    root = module.split(".")[0]
    if root in FORBIDDEN_MODULES:
        return True
    if root != "cookcode" or module == "cookcode":
        return False
    return not any(
        module == allowed or module.startswith(allowed + ".") for allowed in allowed_harness
    )


def _scan_source(source: str, package: list[str], allowed_harness: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            base = package[: len(package) - (node.level - 1)]
            if node.module:
                imported = [".".join([*base, node.module])]
            else:
                imported = [".".join([*base, alias.name]) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module == "cookcode":
            imported = [
                "cookcode" if alias.name == "__version__" else f"cookcode.{alias.name}"
                for alias in node.names
            ]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported = [node.module]
        violations.extend(f"imports {m}" for m in imported if _import_violation(m, allowed_harness))
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in FORBIDDEN_CALLS:
                violations.append(f"calls {name}")
    return violations


def scan_agent_source(source: str) -> list[str]:
    return _scan_source(source, ["cookcode", "agent"], AGENT_ALLOWED_HARNESS)


def scan_tools_source(source: str) -> list[str]:
    return _scan_source(source, ["cookcode", "tools"], TOOLS_ALLOWED_HARNESS)


def test_agent_scan_accepts_allowed_imports() -> None:
    source = (
        "from cookcode import __version__\n"
        "from cookcode.core.events import Event\n"
        "from cookcode.agent.tools import Tool\n"
        "from cookcode.store.base import SessionLog\n"
        "from cookcode.providers.base import Provider\n"
        "from cookcode.env.base import Environment\n"
        "from .tools import Tool\n"
        "from ..core.events import Event\n"
        "import asyncio\n"
    )
    assert scan_agent_source(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from cookcode.store.jsonl import JsonlSessionStore",
        "from cookcode.providers.fake import FakeProvider",
        "from cookcode.env.local import LocalEnvironment",
        "import cookcode.chaos",
        "from cookcode.chaos.scenario import ScenarioProvider",
        "from cookcode.store import jsonl",
        "from cookcode import chaos",
        "from ..store.jsonl import JsonlSessionStore",
        "from .. import chaos",
        "from ..providers import fake",
        "import cookcode.tools",
        "from cookcode.tools.files import read_tool",
        "from cookcode import tools",
        "from ..tools import files",
        "from cookcode.store.checkpoints import FileCheckpointStore",
        "from cookcode.store import checkpoints",
        "import subprocess",
        "import socket",
        "from http import client",
        "import urllib.request",
        "import sys",
        "print('x')",
        "input()",
        "open('f')",
        "path.open()",
    ],
)
def test_agent_scan_catches_violations(source: str) -> None:
    assert scan_agent_source(source)


def test_agent_package_respects_boundaries() -> None:
    files = sorted(AGENT.rglob("*.py"))
    assert files
    for path in files:
        assert scan_agent_source(path.read_text()) == [], path.name


def test_tools_scan_accepts_allowed_imports() -> None:
    source = (
        "from cookcode.core.items import ToolCall\n"
        "from cookcode.agent.tools import Tool, ToolContext, ToolOutput\n"
        "from cookcode.env.base import Environment\n"
        "from cookcode.tools.files import read_tool\n"
        "from .files import read_tool\n"
        "from . import files\n"
        "import shlex\n"
    )
    assert scan_tools_source(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from cookcode.env.local import LocalEnvironment",
        "from cookcode.env import local",
        "from cookcode import env",
        "from cookcode.agent.loop import Agent",
        "from cookcode.agent import loop",
        "from cookcode.agent.gate import decide",
        "from cookcode.store.jsonl import JsonlSessionStore",
        "from cookcode.providers.base import Provider",
        "from cookcode.chaos import scenario",
        "from ..agent.loop import Agent",
        "from ..env.local import LocalEnvironment",
        "import subprocess",
        "import socket",
        "from http import client",
        "import urllib.request",
        "import sys",
        "print('x')",
        "input()",
        "open('f')",
        "path.open()",
    ],
)
def test_tools_scan_catches_violations(source: str) -> None:
    assert scan_tools_source(source)


def test_tools_package_respects_boundaries() -> None:
    files = sorted(TOOLS.rglob("*.py")) if TOOLS.exists() else []
    for path in files:
        assert scan_tools_source(path.read_text()) == [], path.name
