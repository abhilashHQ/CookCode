set -euo pipefail
cat > appconfig/loader.py <<'EOF'
import copy
import os
import tomllib

from .defaults import DEFAULTS

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _coerce(raw, current):
    if isinstance(current, bool):
        lowered = raw.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(f"not a boolean: {raw!r}")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw


def _apply_env(config, env):
    for section, values in config.items():
        if not isinstance(values, dict):
            continue
        for key, current in values.items():
            name = f"APP_{section.upper()}_{key.upper()}"
            if name in env:
                values[key] = _coerce(env[name], current)
    return config


def load_config(path=None, env=None):
    """Return the effective configuration: DEFAULTS < TOML file at ``path`` < APP_* env vars."""
    config = copy.deepcopy(DEFAULTS)
    if path is not None:
        with open(path, "rb") as fh:
            _merge(config, tomllib.load(fh))
    return _apply_env(config, os.environ if env is None else env)
EOF
