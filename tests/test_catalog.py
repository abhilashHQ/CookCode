import pytest

from cookcode.providers.catalog import PRESETS, is_local_url, needs_key


def test_presets_are_in_order_and_complete() -> None:
    assert list(PRESETS) == [
        "nim", "openrouter", "gemini", "ollama", "llamacpp", "lmstudio", "vllm"
    ]
    for preset in PRESETS.values():
        if preset.local:
            assert preset.key_env is None and preset.default_model is None
            assert preset.read_timeout_s == 600
            assert preset.context_window == 4096


def test_existing_presets_keep_their_values() -> None:
    expected = {
        "nim": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY",
                "minimaxai/minimax-m3.1-flash"),
        "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                       "nvidia/nemotron-3.5-super-vl:free"),
        "gemini": ("https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY",
                   "gemini-3.5-flash"),
    }
    for name, values in expected.items():
        preset = PRESETS[name]
        assert (preset.base_url, preset.key_env, preset.default_model) == values
        assert preset.read_timeout_s == 180


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:8080",
        "http://[::1]:1234/v1",
        "http://0.0.0.0:8000/v1",
        "http://localhost",
    ],
)
def test_local_urls(url: str) -> None:
    assert is_local_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost.example.com/v1",
        "https://openrouter.ai/api/v1",
        "http://10.0.0.5:8080/v1",
        "http://localhost:8080.evil.test/v1",
    ],
)
def test_remote_urls(url: str) -> None:
    assert not is_local_url(url)


def test_needs_key() -> None:
    nim = PRESETS["nim"]
    assert needs_key(nim, nim.base_url)
    assert not needs_key(nim, "http://localhost:8000/v1")
    ollama = PRESETS["ollama"]
    assert not needs_key(ollama, ollama.base_url)
    assert not needs_key(ollama, "https://ollama.example.com/v1")
