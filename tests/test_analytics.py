from __future__ import annotations

from datetime import date, timedelta

from quant_app.analytics import analyze_bars, holding_signals


def make_bars(closes: list[float], last_volume: float = 1000) -> list[dict]:
    start = date(2025, 1, 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": last_volume if index == len(closes) - 1 else 500,
        }
        for index, close in enumerate(closes)
    ]


def test_analyze_bars_calculates_trend_metrics() -> None:
    metrics = analyze_bars(make_bars([100 + index for index in range(70)]))

    assert metrics["close"] == 169
    assert metrics["ma20"] == 159.5
    assert metrics["below_ma20_days"] == 0
    assert metrics["return_20d_pct"] > 10
    assert len(metrics["history"]["dates"]) == 70


def test_holding_signals_explain_cost_stop_and_trend_break() -> None:
    closes = [130 - index * 0.4 for index in range(65)] + [102, 100, 98, 95, 91]
    metrics = analyze_bars(make_bars(closes, last_volume=1500))
    holding = {
        "quantity": 500,
        "cost_price": 110,
        "loss_limit_pct": 8,
        "trailing_drawdown_pct": 8,
    }

    signals = holding_signals(holding, metrics, 100000)
    titles = {signal["title"] for signal in signals}

    assert "成本止损线已触发" in titles
    assert "20 日趋势转弱" in titles
    assert all(signal["evidence"] and signal["action"] for signal in signals)
