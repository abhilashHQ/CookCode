import json

from models import Note


class NoteStore:
    def __init__(self):
        self._notes = []

    def create(self, title, body=""):
        note = Note(title, body)
        self._notes.append(note)
        return note

    def add(self, note):
        self._notes.append(note)
        return note

    def by_tag(self, name):
        name = name.strip().lower()
        return [n for n in self._notes if name in n.tags]

    def all_tags(self):
        return sorted({t for n in self._notes for t in n.tags})

    def dump(self):
        return json.dumps([n.to_dict() for n in self._notes], indent=2)

    @classmethod
    def load(cls, text):
        store = cls()
        for item in json.loads(text):
            store.add(Note.from_dict(item))
        return store

    def __len__(self):
        return len(self._notes)
