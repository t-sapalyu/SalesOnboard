from salesonboard.config import Settings


def test_defaults_point_at_local_control_db():
    settings = Settings(_env_file=None)
    assert settings.control_db_url == (
        "postgresql://salesonboard:salesonboard@localhost:5433/control"
    )


def test_environment_overrides_defaults(monkeypatch):
    monkeypatch.setenv("PG_PORT", "6543")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    settings = Settings(_env_file=None)
    assert settings.pg_port == 6543
    assert settings.openai_model == "gpt-4o"


def test_db_url_only_changes_database_name():
    settings = Settings(_env_file=None)
    assert settings.db_url("tenant_company1").endswith(":5433/tenant_company1")


def test_secrets_are_hidden_when_printed():
    settings = Settings(_env_file=None, openai_api_key="sk-secret")
    assert "sk-secret" not in repr(settings)
