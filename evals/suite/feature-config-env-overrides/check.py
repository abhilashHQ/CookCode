import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
import copy
import pytest
from appconfig import DEFAULTS, load_config

ORIGINAL = copy.deepcopy(DEFAULTS)

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("APP_"):
            monkeypatch.delenv(name)

def test_defaults():
    assert load_config()["server"]["port"] == 8080

def test_file_overrides(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text('[server]\nport = 9000\n\n[logging]\nlevel = "DEBUG"\n')
    config = load_config(path)
    assert config["server"]["port"] == 9000
    assert config["server"]["host"] == "127.0.0.1"
    assert config["logging"]["level"] == "DEBUG"

def test_int_with_underscore_key():
    config = load_config(env={"APP_DATABASE_POOL_SIZE": "10"})
    assert config["database"]["pool_size"] == 10 and type(config["database"]["pool_size"]) is int

def test_float():
    assert load_config(env={"APP_DATABASE_TIMEOUT": "3.75"})["database"]["timeout"] == 3.75

def test_str():
    assert load_config(env={"APP_SERVER_HOST": "0.0.0.0"})["server"]["host"] == "0.0.0.0"

@pytest.mark.parametrize("raw, expected", [
    ("1", True), ("true", True), ("YES", True), ("On", True),
    ("0", False), ("False", False), ("no", False), ("OFF", False),
])
def test_bool(raw, expected):
    value = load_config(env={"APP_SERVER_DEBUG": raw})["server"]["debug"]
    assert value is expected

def test_bad_bool_raises():
    with pytest.raises(ValueError):
        load_config(env={"APP_LOGGING_JSON": "maybe"})

def test_bad_int_raises():
    with pytest.raises(ValueError):
        load_config(env={"APP_SERVER_WORKERS": "four"})

def test_unknown_keys_ignored():
    config = load_config(env={"APP_SERVER_COLOR": "blue", "APP_CACHE_SIZE": "3", "OTHER": "x", "APP_": "y"})
    assert "color" not in config["server"] and "cache" not in config
    assert config == ORIGINAL

def test_precedence_env_over_file(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text('[server]\nport = 9000\nworkers = 8\n')
    config = load_config(path, env={"APP_SERVER_PORT": "7000"})
    assert config["server"]["port"] == 7000
    assert config["server"]["workers"] == 8

def test_file_only_key_can_be_overridden(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text('[database]\nreplica_url = "postgres://r"\n')
    config = load_config(path, env={"APP_DATABASE_REPLICA_URL": "postgres://other"})
    assert config["database"]["replica_url"] == "postgres://other"

def test_os_environ_is_default(monkeypatch):
    monkeypatch.setenv("APP_SERVER_PORT", "6000")
    assert load_config()["server"]["port"] == 6000

def test_explicit_env_ignores_os_environ(monkeypatch):
    monkeypatch.setenv("APP_SERVER_PORT", "6000")
    assert load_config(env={})["server"]["port"] == 8080

def test_defaults_not_mutated():
    load_config(env={"APP_SERVER_PORT": "1", "APP_SERVER_DEBUG": "yes", "APP_DATABASE_URL": "x"})
    assert DEFAULTS == ORIGINAL
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


def require_tests(relpath, names):
    path = WORKDIR / relpath
    if not path.is_file():
        fail(f"{relpath} was deleted")
    text = path.read_text()
    missing = [n for n in names if f"def {n}(" not in text]
    if missing:
        fail(f"{relpath} lost tests: {', '.join(missing)}")


require_tests("tests/test_loader.py", ["test_defaults", "test_file_overrides"])
run_hidden_tests(HIDDEN)
sys.exit(0)
