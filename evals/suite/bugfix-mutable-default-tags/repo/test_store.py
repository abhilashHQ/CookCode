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
