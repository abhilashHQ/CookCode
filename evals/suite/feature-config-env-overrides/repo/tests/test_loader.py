from appconfig import DEFAULTS, load_config


def test_defaults():
    assert load_config()["server"]["port"] == 8080


def test_file_overrides(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text('[server]\nport = 9000\n\n[logging]\nlevel = "DEBUG"\n')
    config = load_config(path)
    assert config["server"]["port"] == 9000
    assert config["server"]["host"] == "127.0.0.1"
    assert config["logging"]["level"] == "DEBUG"
    assert DEFAULTS["server"]["port"] == 8080
