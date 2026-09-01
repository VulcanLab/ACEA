from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_uri: str = "postgresql://arena:arena@postgres:5432/arena"
    mongodb_uri: str = "mongodb://mongodb:27017/arena"
    report_composer_port: int = 8005

    # LiteLLM proxy settings for narrative generation
    litellm_base_url: str = ""
    litellm_api_key: str = ""
    # REQUIRED via .env — no default. LiteLLM model name for narrative generation.
    report_model: str

    # Default team objectives (read from .env RED_TEAM_OBJECTIVE / BLUE_TEAM_OBJECTIVE).
    # Used by the narrative generator to populate the Objective Achievement Analysis section.
    red_team_objective: str = ""
    blue_team_objective: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
