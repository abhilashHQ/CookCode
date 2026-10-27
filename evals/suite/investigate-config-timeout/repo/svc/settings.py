import configparser
import os
from pathlib import Path

from .defaults import DEFAULTS

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load(env=None):
    env = os.environ if env is None else env
    name = env.get("APP_ENV", "development")

    parser = configparser.ConfigParser()
    parser.read_dict(DEFAULTS)
    # Later files win; a missing environment file is silently skipped.
    parser.read([CONFIG_DIR / "base.ini", CONFIG_DIR / f"{name}.ini"])

    http = parser["http"]
    timeout = http.getfloat("timeout")
    if "timeout_ms" in http:
        # Older environment files still specify milliseconds.
        timeout = http.getint("timeout_ms") / 1000

    override = env.get("HTTP_TIMEOUT")
    if override:
        timeout = float(override)

    return {
        "env": name,
        "base_url": http.get("base_url"),
        "timeout": timeout,
        "retries": http.getint("retries"),
        "verify_tls": http.getboolean("verify_tls"),
        "cache_ttl": parser["cache"].getint("ttl"),
    }
