import json
from pathlib import Path

_MESSAGES = json.loads((Path(__file__).parent / "locale" / "en.json").read_text())


def t(key, **values):
    return _MESSAGES[key].format(**values)
