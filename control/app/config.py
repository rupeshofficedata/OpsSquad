import os


class Config:
    DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://opssquad:change-me@localhost:5432/opssquad")
    JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-to-a-long-random-string")
    JWT_ALGORITHM = "HS256"
    GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    RUNTIME_URL = os.environ.get("RUNTIME_URL", "http://localhost:8000")
    SYSTEM_JWT = os.environ.get("SYSTEM_JWT", "")  # service-to-service token for webhook-triggered flightplans
