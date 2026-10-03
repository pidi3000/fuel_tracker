import pytest

from app.core import version


@pytest.fixture(autouse=True)
def clear_cache():
    version.get_version.cache_clear()
    yield
    version.get_version.cache_clear()


def test_build_argument_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "1.2.3-test+a1b2c3d")
    assert version.get_version() == "1.2.3-test+a1b2c3d"


def test_falls_back_to_version_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("APP_VERSION", raising=False)
    version_file = tmp_path / "VERSION"
    version_file.write_text("0.4.0\n")
    monkeypatch.setattr(version, "VERSION_FILE", version_file)
    assert version.get_version() == "0.4.0-dev"
