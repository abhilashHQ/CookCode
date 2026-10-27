from models_gen import User


def describe(user: User) -> str:
    return f"{user.name} ({user.id})"
