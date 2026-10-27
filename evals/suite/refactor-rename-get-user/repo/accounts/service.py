from dataclasses import replace

from .repository import UserRepository


class NotFound(Exception):
    pass


class AccountService:
    def __init__(self, repo=None):
        self.repo = repo or UserRepository()

    def profile(self, user_id):
        user = self.repo.get_usr(user_id)
        if user is None or not user.active:
            raise NotFound(user_id)
        return {"id": user.id, "name": user.name, "email": user.email}

    def rename(self, user_id, new_name):
        user = self.repo.get_usr(user_id)
        if user is None:
            raise NotFound(user_id)
        new_name = " ".join(new_name.split())
        if not new_name:
            raise ValueError("name cannot be empty")
        return self.repo.save(replace(user, name=new_name))

    def is_active(self, user_id):
        user = self.repo.get_usr(user_id)
        return bool(user and user.active)
