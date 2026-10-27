from pathlib import Path

import pytest

from logfilter import main

SAMPLE = str(Path(__file__).with_name("sample.log"))


def test_default_level(capsys):
    main([SAMPLE])
    assert len(capsys.readouterr().out.splitlines()) == 7


def test_logger_prefix(capsys):
    main(["-l", "WARNING", "-g", "app.db", SAMPLE])
    out = capsys.readouterr().out
    assert "query timeout" in out and "replica lag" in out and "token" not in out


def test_count(capsys):
    main(["--count", "--since", "2024-04-01T09:10:00", SAMPLE])
    assert capsys.readouterr().out.strip() == "4"


def test_bad_level_exits():
    with pytest.raises(SystemExit) as info:
        main(["-l", "LOUD", SAMPLE])
    assert info.value.code == 2
