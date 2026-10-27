from textutils import excerpt, reading_time, slugify


def test_slugify_basic():
    assert slugify("Hello, World!") == "hello-world"


def test_slugify_unicode():
    assert slugify("Crème brûlée recipes") == "creme-brulee-recipes"


def test_slugify_separator():
    assert slugify("a b  c", separator="_") == "a_b_c"


def test_excerpt():
    assert excerpt("one two three four", limit=12) == "one two..."


def test_reading_time():
    assert reading_time("word " * 460) == 2
