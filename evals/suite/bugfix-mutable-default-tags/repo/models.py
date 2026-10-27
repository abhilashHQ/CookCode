from datetime import datetime, timezone


class Note:
    def __init__(self, title, body="", tags=[], created=None):
        self.title = title
        self.body = body
        self.tags = tags
        self.created = created or datetime.now(timezone.utc)

    def tag(self, *names):
        for name in names:
            name = name.strip().lower()
            if name and name not in self.tags:
                self.tags.append(name)
        return self

    def untag(self, name):
        name = name.strip().lower()
        if name in self.tags:
            self.tags.remove(name)
        return self

    def to_dict(self):
        return {
            "title": self.title,
            "body": self.body,
            "tags": list(self.tags),
            "created": self.created.isoformat(),
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            data["title"],
            data.get("body", ""),
            list(data.get("tags", [])),
            datetime.fromisoformat(data["created"]),
        )

    def __repr__(self):
        return f"Note({self.title!r}, tags={self.tags!r})"
