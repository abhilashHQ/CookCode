import pytest

from accounts import AccountService, NotFound, UserRepository
from accounts.api import handle_admin_lookup, handle_get_profile


def test_get_usr():
    repo = UserRepository()
    assert repo.get_usr(1).name == "Ada Lovelace"
    assert repo.get_usr(99) is None


def test_deactivate():
    repo = UserRepository()
    repo.deactivate(2)
    assert repo.get_usr(2).active is False


def test_profile_hides_inactive():
    with pytest.raises(NotFound):
        AccountService().profile(3)


def test_rename_collapses_spaces():
    service = AccountService()
    service.rename(1, "  Ada   King ")
    assert service.repo.get_usr(1).name == "Ada King"


def test_api():
    service = AccountService()
    assert handle_get_profile(service, "2")[1]["name"] == "Grace Hopper"
    assert handle_admin_lookup(service, "3") == (200, {"id": 3, "email": "linus@example.com", "active": False})
    assert handle_admin_lookup(service, "42")[0] == 404
