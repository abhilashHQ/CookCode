from dataclasses import dataclass, replace


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str
    active: bool = True


_SEED = {
    1: User(1, "ada@example.com", "Ada Lovelace"),
    2: User(2, "grace@example.com", "Grace Hopper"),
    3: User(3, "linus@example.com", "Linus T", active=False),
}


class UserRepository:
    def __init__(self, rows=None):
        self._rows = dict(_SEED if rows is None else rows)

    def get_usr(self, user_id):
        return self._rows.get(user_id)

    def find_by_email(self, email):
        email = email.strip().lower()
        return next((u for u in self._rows.values() if u.email == email), None)

    def add(self, user):
        if user.id in self._rows:
            raise ValueError(f"user {user.id} already exists")
        self._rows[user.id] = user
        return user

    def save(self, user):
        self._rows[user.id] = user
        return user

    def deactivate(self, user_id):
        user = self.get_usr(user_id)
        if user is None:
            raise KeyError(user_id)
        return self.save(replace(user, active=False))
