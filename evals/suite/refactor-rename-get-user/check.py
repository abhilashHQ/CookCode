import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import pytest
from accounts import AccountService, NotFound, UserRepository, User
from accounts.api import handle_admin_lookup, handle_get_profile

def test_new_name_only():
    assert hasattr(UserRepository, "get_user")
    assert not hasattr(UserRepository, "get_usr")
    assert not hasattr(UserRepository(), "get_usr")

def test_get_user():
    repo = UserRepository()
    assert repo.get_user(1).name == "Ada Lovelace"
    assert repo.get_user(99) is None

def test_custom_rows():
    repo = UserRepository({7: User(7, "x@example.com", "X")})
    assert repo.get_user(7).email == "x@example.com" and repo.get_user(1) is None

def test_deactivate():
    repo = UserRepository()
    repo.deactivate(2)
    assert repo.get_user(2).active is False
    with pytest.raises(KeyError):
        repo.deactivate(99)

def test_profile_hides_inactive():
    with pytest.raises(NotFound):
        AccountService().profile(3)

def test_rename_collapses_spaces():
    service = AccountService()
    service.rename(1, "  Ada   King ")
    assert service.repo.get_user(1).name == "Ada King"
    with pytest.raises(NotFound):
        service.rename(50, "Nobody")

def test_is_active():
    service = AccountService()
    assert service.is_active(1) and not service.is_active(3) and not service.is_active(77)

def test_api():
    service = AccountService()
    assert handle_get_profile(service, "2")[1]["name"] == "Grace Hopper"
    assert handle_get_profile(service, "abc")[0] == 404
    assert handle_admin_lookup(service, "3") == (200, {"id": 3, "email": "linus@example.com", "active": False})
    assert handle_admin_lookup(service, "42")[0] == 404
'''


def fail(msg):
    print(msg)
    sys.exit(1)


def run_hidden_tests(source):
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "test_hidden.py"
        test_file.write_text(source)
        env = dict(os.environ, PYTHONPATH=str(WORKDIR), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file)],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=180,
        )
    if proc.returncode != 0:
        lines = proc.stdout.splitlines()
        summary = [l for l in lines if l.startswith(("FAILED", "ERROR"))] or (proc.stdout + proc.stderr).strip().splitlines() or ["no output"]
        fail("hidden tests failed: " + summary[0][:300])


for path in WORKDIR.rglob("*.py"):
    if ".git" in path.parts:
        continue
    if re.search(r"\bget_usr\b", path.read_text()):
        fail(f"get_usr still referenced in {path.relative_to(WORKDIR)}")

tests = WORKDIR / "tests" / "test_accounts.py"
if not tests.is_file():
    fail("tests/test_accounts.py was deleted")
text = tests.read_text()
for name in ("test_deactivate", "test_profile_hides_inactive", "test_rename_collapses_spaces", "test_api"):
    if f"def {name}(" not in text:
        fail(f"tests/test_accounts.py lost {name}")
if text.count("get_user(") < 4:
    fail("tests were not updated to use get_user")

run_hidden_tests(HIDDEN)
sys.exit(0)
