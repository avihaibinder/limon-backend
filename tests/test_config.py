from app.core.config import Settings


def test_cors_origins_accepts_json(monkeypatch) -> None:
    monkeypatch.setenv(
        "LIMON_CORS_ORIGINS",
        '["https://app.example.com", "https://admin.example.com"]',
    )

    settings = Settings(_env_file=None)

    assert settings.cors_origins == [
        "https://app.example.com",
        "https://admin.example.com",
    ]


def test_cors_origins_accepts_comma_separated_values(monkeypatch) -> None:
    monkeypatch.setenv(
        "LIMON_CORS_ORIGINS",
        "https://app.example.com, https://admin.example.com",
    )

    settings = Settings(_env_file=None)

    assert settings.cors_origins == [
        "https://app.example.com",
        "https://admin.example.com",
    ]
