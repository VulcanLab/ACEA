from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # REQUIRED via .env — no default. LiteLLM model name.
    judge_model: str
    judge_violation_threshold: float = 0.7
    judge_rule_keywords: str = ""
    litellm_base_url: str = ""
    litellm_api_key: str = ""
    judge_port: int = 8002

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
