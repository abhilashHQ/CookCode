import pytest

from pagination import paginate


def test_first_page_starts_at_first_item():
    page = paginate(list(range(10)), page=1, per_page=4)
    assert page.items == [0, 1, 2, 3]


def test_page_count_rounds_up():
    assert paginate(list(range(10)), per_page=4).pages == 3


def test_empty_input_has_one_page():
    page = paginate([], page=1)
    assert page.items == []
    assert page.pages == 1
    assert not page.has_next


def test_rejects_page_zero():
    with pytest.raises(ValueError):
        paginate([1, 2, 3], page=0)
