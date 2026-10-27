from .service import AccountService, NotFound


def handle_get_profile(service: AccountService, user_id: str):
    try:
        return 200, service.profile(int(user_id))
    except (NotFound, ValueError):
        return 404, {"error": "not found"}


def handle_admin_lookup(service: AccountService, user_id: str):
    user = service.repo.get_usr(int(user_id))
    if user is None:
        return 404, {"error": "not found"}
    return 200, {"id": user.id, "email": user.email, "active": user.active}
