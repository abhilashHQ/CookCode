import json
from pathlib import Path

import gen
from app import describe
from models_gen import User


def test_describe():
    assert describe(User(id="u1", name="Ada")) == "Ada (u1)"


def test_generated_file_is_fresh():
    here = Path(__file__).parent
    schema = json.loads((here / "schema.json").read_text())
    assert (here / "models_gen.py").read_text() == gen.render(schema)
