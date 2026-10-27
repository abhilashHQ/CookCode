import re
from functools import total_ordering

_VERSION_RE = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+))?"
    r"(?:\+([0-9A-Za-z-]+))?$"
)


@total_ordering
class Version:
    def __init__(self, major, minor, patch, prerelease=None, build=None):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = prerelease
        self.build = build

    @classmethod
    def parse(cls, text):
        match = _VERSION_RE.match(text.strip())
        if not match:
            raise ValueError(f"not a semantic version: {text!r}")
        major, minor, patch, pre, build = match.groups()
        return cls(int(major), int(minor), int(patch), pre, build)

    def _key(self):
        if self.prerelease is None:
            return (self.major, self.minor, self.patch, 1, ())
        parts = tuple(
            (0, int(p), "") if p.isdigit() else (1, 0, p)
            for p in self.prerelease.split(".")
        )
        return (self.major, self.minor, self.patch, 0, parts)

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __hash__(self):
        return hash(self._key())

    def bump(self, part):
        if part == "major":
            return Version(self.major + 1, 0, 0)
        if part == "minor":
            return Version(self.major, self.minor + 1, 0)
        if part == "patch":
            if self.prerelease is not None:
                return Version(self.major, self.minor, self.patch)
            return Version(self.major, self.minor, self.patch + 1)
        raise ValueError(f"unknown part: {part}")

    def __str__(self):
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += f"-{self.prerelease}"
        if self.build:
            text += f"+{self.build}"
        return text

    def __repr__(self):
        return f"Version.parse({str(self)!r})"
