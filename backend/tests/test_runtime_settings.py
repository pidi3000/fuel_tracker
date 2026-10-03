import pytest

from app.core.config import Settings
from app.services.runtime_settings import SPECS, SettingError, normalize
from tests.conftest import AppUnderTest


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("review_before_send", False, False),
        ("match_window_minutes", 15, 15),
        ("tz", " Europe/Paris ", "Europe/Paris"),
        ("fuel_types", "Diesel, Super ,", ["Diesel", "Super"]),
        ("fuel_types", ["A", "B"], ["A", "B"]),
        ("volume_unit", "gal", "gal"),
        ("pace_date_format", "%d.%m.%Y, %H:%M", "%d.%m.%Y, %H:%M"),
    ],
)
def test_valid_values(key: str, value: object, expected: object) -> None:
    assert normalize(key, value) == expected


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("review_before_send", "yes"),
        ("match_window_minutes", 0),
        ("match_window_minutes", "10"),
        ("match_window_minutes", True),
        ("receipt_timeout_minutes", 10**6),
        ("tz", "Mars/Olympus"),
        ("fuel_types", ""),
        ("fuel_types", ["Super", "super"]),
        ("volume_unit", "  "),
        ("pace_date_format", "no codes here"),
        ("imap_password", "x"),
    ],
)
def test_invalid_values(key: str, value: object) -> None:
    with pytest.raises(SettingError):
        normalize(key, value)


def test_environment_value_parsing() -> None:
    settings = Settings(fuel_types="Diesel, Super, Super E10")  # as in an environment variable
    assert settings.fuel_types == ["Diesel", "Super", "Super E10"]


async def test_overrides_win_and_survive_a_restart(api: AppUnderTest) -> None:
    runtime = api.services.runtime
    assert runtime.match_window_minutes == 10
    await runtime.set("match_window_minutes", 20)
    assert runtime.match_window_minutes == 20 and runtime.is_overridden("match_window_minutes")

    from app.services.runtime_settings import RuntimeSettings

    fresh = RuntimeSettings(api.services.settings, api.services.sessionmaker)
    await fresh.load()
    assert fresh.match_window_minutes == 20

    await runtime.reset("match_window_minutes")
    assert runtime.match_window_minutes == 10 and not runtime.is_overridden("match_window_minutes")


def test_every_spec_has_an_environment_default() -> None:
    settings = Settings()
    for key in SPECS:
        assert hasattr(settings, key), key
