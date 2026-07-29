from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from quant_app.app import create_app


def bars(start: float = 100) -> list[dict]:
    day = date(2025, 1, 1)
    return [
        {
            "date": (day + timedelta(days=index)).isoformat(),
            "open": start + index * 0.2,
            "high": start + index * 0.2 + 1,
            "low": start + index * 0.2 - 1,
            "close": start + index * 0.2,
            "volume": 100000 + index,
        }
        for index in range(90)
    ]


class FakeProvider:
    def fetch_us_daily(self, symbol: str) -> dict:
        return {"symbol": symbol, "name": symbol, "source": "test", "bars": bars()}

    def fetch_cn_daily(self, symbol: str) -> dict:
        return {"symbol": symbol, "name": symbol, "source": "test", "bars": bars(4)}

    def close(self) -> None:
        pass


def test_dashboard_and_holding_crud(tmp_path) -> None:
    app = create_app(
        db_path=tmp_path / "test.db",
        provider_factory=FakeProvider,
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/").status_code == 200

        created = client.post(
            "/api/holdings",
            json={
                "symbol": "600519",
                "name": "贵州茅台",
                "asset_type": "股票",
                "quantity": 100,
                "cost_price": 1400,
                "loss_limit_pct": 8,
                "trailing_drawdown_pct": 8,
                "enabled": True,
            },
        )
        assert created.status_code == 201
        holding_id = created.json()["id"]

        client.post("/api/refresh")
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["portfolio"]["capital"] == 100000
        assert any(item["symbol"] == "sh600519" for item in dashboard["holdings"])

        updated = client.put(
            f"/api/holdings/{holding_id}",
            json={**created.json(), "quantity": 200},
        )
        assert updated.status_code == 200
        assert updated.json()["quantity"] == 200
        assert client.delete(f"/api/holdings/{holding_id}").status_code == 204
