# Built-in fallbacks. Everything here can be overridden by config/*.ini.
DEFAULTS = {
    "http": {
        "base_url": "http://localhost:9000",
        "timeout": "30",
        "retries": "3",
        "verify_tls": "yes",
    },
    "cache": {
        "ttl": "300",
    },
}
