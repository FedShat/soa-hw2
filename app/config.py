import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://marketplace:marketplace@localhost:5433/marketplace"
    )
    jwt_secret: str = os.getenv("JWT_SECRET", "local-homework-secret-at-least-32-characters")
    order_interval_minutes: float = float(os.getenv("ORDER_INTERVAL_MINUTES", "1"))
    access_minutes: int = 20
    refresh_days: int = 14


settings = Settings()
