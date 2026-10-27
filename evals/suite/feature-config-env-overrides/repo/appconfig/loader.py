import copy
import tomllib

from .defaults import DEFAULTS


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path=None):
    """Return the effective configuration: DEFAULTS merged with the TOML file at ``path``."""
    config = copy.deepcopy(DEFAULTS)
    if path is not None:
        with open(path, "rb") as fh:
            _merge(config, tomllib.load(fh))
    return config
