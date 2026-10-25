"""All settings in one place, read from environment variables or .env."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres server. One control database, plus one database per customer
    # on the same server, so only the database name changes between them.
    pg_host: str = "localhost"
    pg_port: int = 5433
    pg_user: str = "salesonboard"
    pg_password: SecretStr = SecretStr("salesonboard")
    control_db: str = "control"

    # OpenAI. Model is config, not code, so it can change without a release.
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"

    # Euros per 1M tokens, from OpenAI's pricing page converted to EUR.
    # Update these when the model or the price changes.
    price_input_per_mtok_eur: float = 0.14
    price_output_per_mtok_eur: float = 0.55

    # Total LLM spend allowed for one batch, across both agents and all rounds.
    batch_cost_limit_eur: float = 1.0

    def db_url(self, dbname: str) -> str:
        """Connection string for one database on the server."""
        password = self.pg_password.get_secret_value()
        return f"postgresql://{self.pg_user}:{password}@{self.pg_host}:{self.pg_port}/{dbname}"

    @property
    def control_db_url(self) -> str:
        return self.db_url(self.control_db)


@lru_cache
def get_settings() -> Settings:
    return Settings()
