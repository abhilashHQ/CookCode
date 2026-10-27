from wordstats import count, main


def test_count():
    assert count("one two\nthree\n") == {"lines": 2, "words": 3, "chars": 14}


def test_table_output(tmp_path, capsys):
    a = tmp_path / "a.txt"
    a.write_text("hello world\n")
    b = tmp_path / "b.txt"
    b.write_text("x\ny\n")
    main([str(a), str(b)])
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["1", "2", "12", str(a)]
    assert out[2].split() == ["3", "4", "16", "total"]
