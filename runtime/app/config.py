from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://opssquad:change-me@localhost:5432/opssquad"
    redis_url: str = "redis://localhost:6379"
    jwt_secret: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    anthropic_api_key: str = ""
    intent_router_model: str = "claude-haiku-4-5-20251001"
    agent_model: str = "claude-sonnet-5"
    mutating_agents_enabled: bool = True

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
