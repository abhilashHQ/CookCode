import ast
from pathlib import Path

import pytest

CORE = Path(__file__).parents[1] / "src" / "harness" / "core"
FORBIDDEN_MODULES = {"subprocess", "socket", "http", "urllib", "sys", "httpx", "requests"}
FORBIDDEN_CALLS = {"print", "input", "open"}
ALLOWED_HARNESS_IMPORTS = {"harness.core"}


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
            if module.split(".")[0] == "harness":
                assert any(
                    module == allowed or module.startswith(allowed + ".")
                    for allowed in ALLOWED_HARNESS_IMPORTS
                ), f"{path.name} imports {module}"


def test_core_never_calls_print_input_open_or_reads_environ() -> None:
    for path in _core_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_CALLS, f"{path.name} calls {node.func.id}"
            if isinstance(node, ast.Attribute):
                assert node.attr != "environ", f"{path.name} reads os.environ"


AGENT = Path(__file__).parents[1] / "src" / "harness" / "agent"
TOOLS = Path(__file__).parents[1] / "src" / "harness" / "tools"
AGENT_ALLOWED_HARNESS = (
    "harness.core",
    "harness.agent",
    "harness.store.base",
    "harness.providers.base",
    "harness.env.base",
)
TOOLS_ALLOWED_HARNESS = (
    "harness.core",
    "harness.agent.tools",
    "harness.env.base",
    "harness.tools",
)


def _import_violation(module: str, allowed_harness: tuple[str, ...]) -> bool:
    root = module.split(".")[0]
    if root in FORBIDDEN_MODULES:
        return True
    if root != "harness" or module == "harness":
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
        elif isinstance(node, ast.ImportFrom) and node.module == "harness":
            imported = [
                "harness" if alias.name == "__version__" else f"harness.{alias.name}"
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
    return _scan_source(source, ["harness", "agent"], AGENT_ALLOWED_HARNESS)


def scan_tools_source(source: str) -> list[str]:
    return _scan_source(source, ["harness", "tools"], TOOLS_ALLOWED_HARNESS)


def test_agent_scan_accepts_allowed_imports() -> None:
    source = (
        "from harness import __version__\n"
        "from harness.core.events import Event\n"
        "from harness.agent.tools import Tool\n"
        "from harness.store.base import SessionLog\n"
        "from harness.providers.base import Provider\n"
        "from harness.env.base import Environment\n"
        "from .tools import Tool\n"
        "from ..core.events import Event\n"
        "import asyncio\n"
    )
    assert scan_agent_source(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from harness.store.jsonl import JsonlSessionStore",
        "from harness.providers.fake import FakeProvider",
        "from harness.env.local import LocalEnvironment",
        "import harness.chaos",
        "from harness.chaos.scenario import ScenarioProvider",
        "from harness.store import jsonl",
        "from harness import chaos",
        "from ..store.jsonl import JsonlSessionStore",
        "from .. import chaos",
        "from ..providers import fake",
        "import harness.tools",
        "from harness.tools.files import read_tool",
        "from harness import tools",
        "from ..tools import files",
        "from harness.store.checkpoints import FileCheckpointStore",
        "from harness.store import checkpoints",
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
        "from harness.core.items import ToolCall\n"
        "from harness.agent.tools import Tool, ToolContext, ToolOutput\n"
        "from harness.env.base import Environment\n"
        "from harness.tools.files import read_tool\n"
        "from .files import read_tool\n"
        "from . import files\n"
        "import shlex\n"
    )
    assert scan_tools_source(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from harness.env.local import LocalEnvironment",
        "from harness.env import local",
        "from harness import env",
        "from harness.agent.loop import Agent",
        "from harness.agent import loop",
        "from harness.agent.gate import decide",
        "from harness.store.jsonl import JsonlSessionStore",
        "from harness.providers.base import Provider",
        "from harness.chaos import scenario",
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
