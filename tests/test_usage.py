from cookcode.core.usage import Usage


def test_add_sums_every_field() -> None:
    a = Usage(input=10, cache_read=5, cache_write=1, output=3, reasoning=2)
    b = Usage(input=1, cache_read=1, cache_write=1, output=1, reasoning=1)
    assert a + b == Usage(input=11, cache_read=6, cache_write=2, output=4, reasoning=3)


def test_total_input_counts_cached_tokens() -> None:
    assert Usage(input=100, cache_read=900, cache_write=50).total_input == 1050
