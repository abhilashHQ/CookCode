import pytest

from release import next_version
from versioning import Version


def test_parse_plain():
    v = Version.parse("v1.4.2")
    assert (v.major, v.minor, v.patch, v.prerelease) == (1, 4, 2, None)


def test_parse_release_candidate():
    v = Version.parse("2.0.0-rc.1")
    assert v.prerelease == "rc.1"


def test_prerelease_sorts_before_release():
    assert Version.parse("1.0.0-beta") < Version.parse("1.0.0")


@pytest.mark.parametrize("bad", ["1.2", "1.2.3.4", "01.2.3", "1.2.3-"])
def test_rejects_invalid(bad):
    with pytest.raises(ValueError):
        Version.parse(bad)


def test_next_version():
    assert next_version("1.4.2", "minor") == "1.5.0"
