from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SECRET_KEY: str = "insecure-dev-key-change-me"
    DATABASE_URL: str = "sqlite:///./data/jsw_cf.db"
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "changeme123"
    ENV: str = "development"


settings = Settings()
