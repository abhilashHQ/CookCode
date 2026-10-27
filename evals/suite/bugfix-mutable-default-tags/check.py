import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(sys.argv[1]).resolve()

HIDDEN = r'''
from models import Note
from store import NoteStore

def test_tag_normalizes_and_dedupes():
    note = Note("groceries", tags=["home"])
    note.tag(" Errands ", "home", "")
    assert note.tags == ["home", "errands"]

def test_by_tag():
    store = NoteStore()
    store.add(Note("a", tags=["work"]))
    store.add(Note("b", tags=["home"]))
    assert [n.title for n in store.by_tag("WORK")] == ["a"]

def test_roundtrip():
    store = NoteStore()
    store.add(Note("a", "body", tags=["x", "y"]))
    again = NoteStore.load(store.dump())
    assert again.by_tag("y")[0].body == "body"

def test_default_tags_not_shared():
    first = Note("first")
    first.tag("urgent")
    second = Note("second")
    assert second.tags == []
    assert first.tags == ["urgent"]

def test_store_create_isolated():
    store = NoteStore()
    a = store.create("a")
    store.create("b")
    a.tag("work")
    assert [n.title for n in store.by_tag("work")] == ["a"]
    assert store.all_tags() == ["work"]

def test_untag_on_one_note_only():
    a, b = Note("a"), Note("b")
    a.tag("x"); b.tag("x")
    a.untag("x")
    assert a.tags == [] and b.tags == ["x"]
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


require_tests("test_store.py", ["test_tag_normalizes_and_dedupes", "test_by_tag", "test_roundtrip"])
run_hidden_tests(HIDDEN)
sys.exit(0)
