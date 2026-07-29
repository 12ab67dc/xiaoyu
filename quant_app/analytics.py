from __future__ import annotations

import math
import statistics
from typing import Any, Sequence


def _pct_change(current: float, previous: float | None) -> float | None:
    if previous in (None, 0):
        return None
    return (current / previous - 1) * 100


def _mean(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _series_ma(values: Sequence[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        result.append(running / window if index + 1 >= window else None)
    return result


def rsi(values: Sequence[float], window: int = 14) -> float | None:
    if len(values) <= window:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    recent = changes[-window:]
    gains = sum(max(change, 0) for change in recent) / window
    losses = sum(abs(min(change, 0)) for change in recent) / window
    if losses == 0:
        return 100.0
    relative_strength = gains / losses
    return 100 - 100 / (1 + relative_strength)


def annualized_volatility(values: Sequence[float]) -> float | None:
    if len(values) < 3:
        return None
    returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1]]
    if len(returns) < 2:
        return None
    return statistics.stdev(returns[-20:]) * math.sqrt(252) * 100


def analyze_bars(bars: Sequence[dict[str, Any]]) -> dict[str, Any]:
    clean = [bar for bar in bars if bar.get("close") not in (None, 0)]
    if len(clean) < 2:
        raise ValueError("At least two valid bars are required")
    closes = [float(bar["close"]) for bar in clean]
    volumes = [float(bar.get("volume") or 0) for bar in clean]
    latest = clean[-1]
    ma20_series = _series_ma(closes, 20)
    ma20 = ma20_series[-1]
    ma20_previous = ma20_series[-6] if len(ma20_series) >= 6 else None
    high_20 = max(closes[-20:])
    return {
        "as_of": latest["date"],
        "close": round(closes[-1], 4),
        "daily_change_pct": _pct_change(closes[-1], closes[-2]),
        "return_5d_pct": _pct_change(closes[-1], closes[-6] if len(closes) >= 6 else None),
        "return_20d_pct": _pct_change(closes[-1], closes[-21] if len(closes) >= 21 else None),
        "ma5": _mean(closes, 5),
        "ma20": ma20,
        "ma60": _mean(closes, 60),
        "ma20_slope_5d_pct": _pct_change(ma20, ma20_previous) if ma20 else None,
        "drawdown_20d_pct": _pct_change(closes[-1], high_20),
        "rsi14": rsi(closes),
        "volatility_20d_pct": annualized_volatility(closes),
        "volume_ratio_5d": (
            volumes[-1] / (sum(volumes[-6:-1]) / 5)
            if len(volumes) >= 6 and sum(volumes[-6:-1]) > 0
            else None
        ),
        "below_ma20_days": _count_below_ma(closes, ma20_series),
        "history": {
            "dates": [bar["date"] for bar in clean[-90:]],
            "close": [round(float(bar["close"]), 4) for bar in clean[-90:]],
            "ma20": [round(value, 4) if value is not None else None for value in ma20_series[-90:]],
        },
    }


def _count_below_ma(closes: Sequence[float], ma_values: Sequence[float | None]) -> int:
    count = 0
    for close, moving_average in zip(reversed(closes), reversed(ma_values)):
        if moving_average is None or close >= moving_average:
            break
        count += 1
    return count


def holding_signals(
    holding: dict[str, Any],
    metrics: dict[str, Any],
    portfolio_capital: float,
) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    close = float(metrics["close"])
    cost = float(holding.get("cost_price") or 0)
    quantity = float(holding.get("quantity") or 0)
    loss_limit = float(holding.get("loss_limit_pct") or 8)
    drawdown_limit = float(holding.get("trailing_drawdown_pct") or 8)

    if quantity > 0 and cost > 0:
        pnl_pct = _pct_change(close, cost) or 0
        if pnl_pct <= -loss_limit:
            signals.append(
                _signal(
                    "critical",
                    "成本止损线已触发",
                    f"现价较持仓成本低 {abs(pnl_pct):.1f}%，超过设置的 {loss_limit:.1f}% 风险线。",
                    "优先复核基本面和仓位计划；若原交易逻辑已失效，应执行预设退出纪律。",
                )
            )

    drawdown = metrics.get("drawdown_20d_pct")
    if drawdown is not None and drawdown <= -drawdown_limit:
        signals.append(
            _signal(
                "high",
                "短期回撤扩大",
                f"现价距近 20 日收盘高点回撤 {abs(drawdown):.1f}%，超过 {drawdown_limit:.1f}% 阈值。",
                "检查是否跌破关键支撑；已有盈利仓位可考虑分批降低风险敞口。",
            )
        )

    if metrics.get("below_ma20_days", 0) >= 2 and (metrics.get("ma20_slope_5d_pct") or 0) < 0:
        signals.append(
            _signal(
                "high",
                "20 日趋势转弱",
                f"收盘价已连续 {metrics['below_ma20_days']} 日位于 20 日均线下方，且均线斜率为负。",
                "这代表中短期趋势确认转弱，建议减少主观补仓，等待重新站回均线。",
            )
        )

    if (
        (metrics.get("daily_change_pct") or 0) <= -2
        and (metrics.get("volume_ratio_5d") or 0) >= 1.8
    ):
        signals.append(
            _signal(
                "medium",
                "放量下跌",
                f"当日下跌 {abs(metrics['daily_change_pct']):.1f}%，成交量为近 5 日均量的 {metrics['volume_ratio_5d']:.1f} 倍。",
                "放量下跌可能意味着抛压增强，下一交易日重点观察低点能否守住。",
            )
        )

    market_value = quantity * close
    if portfolio_capital > 0 and market_value / portfolio_capital >= 0.30:
        weight = market_value / portfolio_capital * 100
        signals.append(
            _signal(
                "medium",
                "单一标的仓位集中",
                f"按总资金估算，该标的仓位约为 {weight:.1f}%，超过 30% 提醒线。",
                "单一标的波动会显著影响组合，建议结合总风险预算评估是否分散。",
            )
        )

    if not signals:
        signals.append(
            _signal(
                "normal",
                "暂未触发退出风险",
                "价格、趋势和回撤均未触发当前规则阈值。",
                "继续按日频计划观察，不因单日噪声频繁交易。",
            )
        )
    return signals


def _signal(level: str, title: str, evidence: str, action: str) -> dict[str, str]:
    return {"level": level, "title": title, "evidence": evidence, "action": action}
