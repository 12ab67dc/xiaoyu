from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("QUANT_HOST", "127.0.0.1")
    port: int = int(os.getenv("QUANT_PORT", "8765"))
    capital: float = float(os.getenv("QUANT_CAPITAL", "100000"))
    timezone: str = os.getenv("QUANT_TIMEZONE", "Asia/Shanghai")
    morning_refresh: str = os.getenv("QUANT_MORNING_REFRESH", "07:30")
    holdings_refresh: str = os.getenv("QUANT_HOLDINGS_REFRESH", "15:10")
    db_path: Path = Path(
        os.getenv("QUANT_DB_PATH", str(PACKAGE_DIR / "data" / "quant_app.db"))
    )
    request_timeout: float = float(os.getenv("QUANT_REQUEST_TIMEOUT", "15"))


settings = Settings()

