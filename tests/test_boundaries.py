import ast
from pathlib import Path

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
