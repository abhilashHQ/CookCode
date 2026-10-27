DEFAULTS = {
    "server": {
        "host": "127.0.0.1",
        "port": 8080,
        "debug": False,
        "workers": 2,
    },
    "database": {
        "url": "sqlite:///app.db",
        "pool_size": 5,
        "timeout": 2.5,
    },
    "logging": {
        "level": "INFO",
        "json": False,
    },
}
