import ast
from pathlib import Path

from cli import main


def test_greet(capsys):
    assert main(["greet", "Ada"]) == 0
    assert capsys.readouterr().out == "Hello, Ada!\n"


def test_no_literal_user_text():
    """Every printed string goes through t() so it can be translated."""
    tree = ast.parse((Path(__file__).parent / "cli.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
            for arg in node.args:
                assert not isinstance(arg, (ast.Constant, ast.JoinedStr)), ast.dump(arg)
