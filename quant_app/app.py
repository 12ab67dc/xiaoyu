from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from .db import Database
from .providers import MarketDataProvider, normalize_cn_symbol
from .service import QuantService
from .settings import PACKAGE_DIR, settings


class HoldingPayload(BaseModel):
    symbol: str
    name: str = Field(min_length=1, max_length=40)
    asset_type: str = Field(default="股票", max_length=12)
    quantity: float = Field(default=0, ge=0)
    cost_price: float = Field(default=0, ge=0)
    loss_limit_pct: float = Field(default=8, ge=1, le=30)
    trailing_drawdown_pct: float = Field(default=8, ge=1, le=30)
    enabled: bool = True

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalize_cn_symbol(value)


class SettingsPayload(BaseModel):
    portfolio_capital: float = Field(gt=0, le=100_000_000)


def _parse_clock(value: str) -> tuple[int, int]:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        if hour not in range(24) or minute not in range(60):
            raise ValueError
        return hour, minute
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"无效时间配置: {value}") from exc


def create_app(
    db_path: Path | None = None,
    provider_factory: Any | None = None,
    enable_scheduler: bool = True,
) -> FastAPI:
    db = Database(db_path or settings.db_path)
    factory = provider_factory or (lambda: MarketDataProvider(settings.request_timeout))
    service = QuantService(db, factory, settings.timezone, settings.capital)
    scheduler = BackgroundScheduler(timezone=settings.timezone)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.initialize(settings.capital)
        if enable_scheduler:
            morning_hour, morning_minute = _parse_clock(settings.morning_refresh)
            holding_hour, holding_minute = _parse_clock(settings.holdings_refresh)
            scheduler.add_job(
                service.refresh_us_report,
                "cron",
                day_of_week="mon-fri",
                hour=morning_hour,
                minute=morning_minute,
                id="morning_report",
                replace_existing=True,
                max_instances=1,
            )
            scheduler.add_job(
                service.refresh_holdings,
                "cron",
                day_of_week="mon-fri",
                hour=holding_hour,
                minute=holding_minute,
                id="holdings_refresh",
                replace_existing=True,
                max_instances=1,
            )
            scheduler.start()
        if db.latest_report() is None:
            asyncio.create_task(asyncio.to_thread(service.refresh_all))
        yield
        if scheduler.running:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="A 股决策台", version="0.1.0", lifespan=lifespan)
    app.state.db = db
    app.state.service = service
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return templates.TemplateResponse(request=request, name="index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/dashboard")
    def dashboard() -> dict[str, Any]:
        return service.dashboard()

    @app.post("/api/refresh", status_code=status.HTTP_202_ACCEPTED)
    def refresh(background_tasks: BackgroundTasks) -> dict[str, Any]:
        if service.status()["running"]:
            return {"accepted": False, "message": "刷新正在进行"}
        background_tasks.add_task(service.refresh_all)
        return {"accepted": True, "message": "已开始刷新行情"}

    @app.get("/api/holdings")
    def holdings() -> list[dict[str, Any]]:
        return db.list_holdings()

    @app.post("/api/holdings", status_code=status.HTTP_201_CREATED)
    def create_holding(payload: HoldingPayload) -> dict[str, Any]:
        try:
            return db.upsert_holding(payload.model_dump())
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise HTTPException(409, "该代码已在自选列表中") from exc
            raise

    @app.put("/api/holdings/{holding_id}")
    def update_holding(holding_id: int, payload: HoldingPayload) -> dict[str, Any]:
        if db.get_holding(holding_id) is None:
            raise HTTPException(404, "未找到该标的")
        try:
            return db.upsert_holding(payload.model_dump(), holding_id)
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise HTTPException(409, "该代码已在自选列表中") from exc
            raise

    @app.delete("/api/holdings/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_holding(holding_id: int) -> None:
        if not db.delete_holding(holding_id):
            raise HTTPException(404, "未找到该标的")

    @app.get("/api/market/{symbol}")
    def market(symbol: str) -> dict[str, Any]:
        try:
            normalized = normalize_cn_symbol(symbol)
            return service.market_detail(normalized)
        except (KeyError, ValueError) as exc:
            raise HTTPException(404, "未找到该标的或行情") from exc

    @app.put("/api/settings")
    def update_settings(payload: SettingsPayload) -> dict[str, float]:
        db.set_setting("portfolio_capital", str(payload.portfolio_capital))
        return {"portfolio_capital": payload.portfolio_capital}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("quant_app.app:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
