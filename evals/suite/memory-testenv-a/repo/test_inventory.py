import pytest

from inventory import Inventory


def test_restock():
    inv = Inventory()
    inv.restock("bolt", 5)
    assert inv.count("bolt") == 5


def test_remove_too_many():
    inv = Inventory()
    with pytest.raises(ValueError):
        inv.remove("bolt", 1)
