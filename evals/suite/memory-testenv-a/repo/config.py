import os


def settings():
    env = os.environ.get("APP_ENV", "production")
    if env != "test":
        raise RuntimeError(f"refusing to run against {env} settings; see run_tests.sh")
    return {"max_stock": 1000}
