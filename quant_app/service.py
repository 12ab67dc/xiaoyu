from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .analytics import analyze_bars, holding_signals
from .db import Database
from .providers import MarketDataProvider


US_WATCHLIST = (
    ("QQQ", "纳斯达克100 ETF", "科技风向"),
    ("SOXX", "半导体 ETF", "产业景气"),
    ("SPY", "标普500 ETF", "市场广度"),
    ("NVDA", "英伟达", "AI 算力"),
    ("MSFT", "微软", "云与软件"),
    ("AAPL", "苹果", "消费电子"),
    ("GOOGL", "谷歌", "互联网平台"),
    ("AMZN", "亚马逊", "云与消费"),
    ("META", "Meta", "互联网平台"),
    ("TSLA", "特斯拉", "智能汽车"),
    ("^VIX", "VIX 恐慌指数", "风险偏好"),
)

LEVEL_ORDER = {"normal": 0, "medium": 1, "high": 2, "critical": 3}


class QuantService:
    def __init__(
        self,
        db: Database,
        provider_factory: Callable[[], MarketDataProvider],
        timezone: str = "Asia/Shanghai",
        default_capital: float = 100000,
    ):
        self.db = db
        self.provider_factory = provider_factory
        self.timezone = ZoneInfo(timezone)
        self.default_capital = default_capital
        self._refresh_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status: dict[str, Any] = {
            "running": False,
            "last_started_at": None,
            "last_finished_at": None,
            "last_error": None,
        }

    def refresh_all(self) -> dict[str, Any]:
        if not self._refresh_lock.acquire(blocking=False):
            return {"accepted": False, "message": "已有刷新任务正在运行"}
        self._set_status(running=True, last_started_at=self._now(), last_error=None)
        try:
            report = self.refresh_us_report()
            holdings = self.refresh_holdings()
            self._set_status(running=False, last_finished_at=self._now())
            return {"accepted": True, "report": report, "holdings": holdings}
        except Exception as exc:
            self._set_status(
                running=False,
                last_finished_at=self._now(),
                last_error=str(exc),
            )
            raise
        finally:
            self._refresh_lock.release()

    def refresh_us_report(self) -> dict[str, Any]:
        provider = self.provider_factory()
        items: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            for symbol, name, theme in US_WATCHLIST:
                payload = self._fetch_with_cache(
                    provider.fetch_us_daily, "us", symbol, symbol
                )
                if payload is None:
                    errors.append(f"{symbol} 暂无可用数据")
                    continue
                try:
                    metrics = analyze_bars(payload["bars"])
                except (KeyError, ValueError) as exc:
                    errors.append(f"{symbol}: {exc}")
                    continue
                items.append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "theme": theme,
                        "source": payload.get("source", ""),
                        **metrics,
                    }
                )
        finally:
            provider.close()

        report = self._build_us_report(items, errors)
        self.db.save_report(report["report_date"], report)
        return report

    def refresh_holdings(self) -> list[dict[str, Any]]:
        provider = self.provider_factory()
        capital = self.portfolio_capital()
        results: list[dict[str, Any]] = []
        try:
            for holding in self.db.list_holdings():
                if not holding["enabled"]:
                    continue
                symbol = holding["symbol"]
                payload = self._fetch_with_cache(
                    provider.fetch_cn_daily, "cn", symbol, symbol
                )
                if payload is None:
                    results.append(
                        {
                            **holding,
                            "error": "行情获取失败且没有历史缓存",
                            "signals": [],
                        }
                    )
                    continue
                try:
                    metrics = analyze_bars(payload["bars"])
                    signals = holding_signals(holding, metrics, capital)
                    market_value = float(holding["quantity"]) * metrics["close"]
                    pnl = (
                        (metrics["close"] / float(holding["cost_price"]) - 1) * 100
                        if holding["quantity"] > 0 and holding["cost_price"] > 0
                        else None
                    )
                    results.append(
                        {
                            **holding,
                            "market_name": payload.get("name", holding["name"]),
                            "source": payload.get("source", ""),
                            "metrics": metrics,
                            "signals": signals,
                            "market_value": round(market_value, 2),
                            "portfolio_weight_pct": round(market_value / capital * 100, 2)
                            if capital
                            else 0,
                            "pnl_pct": round(pnl, 2) if pnl is not None else None,
                            "risk_level": max(
                                signals,
                                key=lambda item: LEVEL_ORDER.get(item["level"], 0),
                            )["level"],
                        }
                    )
                except (KeyError, ValueError, ZeroDivisionError) as exc:
                    results.append({**holding, "error": str(exc), "signals": []})
        finally:
            provider.close()
        return results

    def dashboard(self) -> dict[str, Any]:
        report = self.db.latest_report()
        holdings = self._holdings_from_cache()
        actual = [item for item in holdings if float(item.get("quantity") or 0) > 0]
        total_value = sum(float(item.get("market_value") or 0) for item in actual)
        risk_counts = {"critical": 0, "high": 0, "medium": 0, "normal": 0}
        for item in actual:
            level = item.get("risk_level")
            if level in risk_counts:
                risk_counts[level] += 1
        return {
            "generated_at": self._now(),
            "report": report,
            "holdings": holdings,
            "portfolio": {
                "capital": self.portfolio_capital(),
                "invested_value": round(total_value, 2),
                "cash_estimate": round(max(self.portfolio_capital() - total_value, 0), 2),
                "holding_count": len(actual),
                "risk_counts": risk_counts,
            },
            "refresh": self.status(),
        }

    def market_detail(self, symbol: str) -> dict[str, Any]:
        holding = next(
            (item for item in self.db.list_holdings() if item["symbol"] == symbol), None
        )
        if holding is None:
            raise KeyError(symbol)
        payload = self.db.load_cache("cn", symbol)
        if payload is None:
            provider = self.provider_factory()
            try:
                payload = provider.fetch_cn_daily(symbol)
                self.db.save_cache("cn", symbol, payload)
            finally:
                provider.close()
        metrics = analyze_bars(payload["bars"])
        return {"holding": holding, "metrics": metrics, "source": payload.get("source", "")}

    def portfolio_capital(self) -> float:
        try:
            return float(self.db.get_setting("portfolio_capital", str(self.default_capital)))
        except ValueError:
            return self.default_capital

    def status(self) -> dict[str, Any]:
        with self._status_lock:
            return dict(self._status)

    def _holdings_from_cache(self) -> list[dict[str, Any]]:
        capital = self.portfolio_capital()
        results = []
        for holding in self.db.list_holdings():
            payload = self.db.load_cache("cn", holding["symbol"])
            if not payload:
                results.append({**holding, "metrics": None, "signals": [], "risk_level": "normal"})
                continue
            try:
                metrics = analyze_bars(payload["bars"])
                signals = holding_signals(holding, metrics, capital)
                market_value = float(holding["quantity"]) * metrics["close"]
                pnl = (
                    (metrics["close"] / float(holding["cost_price"]) - 1) * 100
                    if holding["quantity"] > 0 and holding["cost_price"] > 0
                    else None
                )
                results.append(
                    {
                        **holding,
                        "market_name": payload.get("name", holding["name"]),
                        "metrics": metrics,
                        "signals": signals,
                        "source": payload.get("source", ""),
                        "market_value": round(market_value, 2),
                        "portfolio_weight_pct": round(market_value / capital * 100, 2) if capital else 0,
                        "pnl_pct": round(pnl, 2) if pnl is not None else None,
                        "risk_level": max(
                            signals, key=lambda item: LEVEL_ORDER.get(item["level"], 0)
                        )["level"],
                    }
                )
            except (KeyError, ValueError, ZeroDivisionError) as exc:
                results.append({**holding, "metrics": None, "signals": [], "error": str(exc)})
        return results

    def _fetch_with_cache(
        self,
        fetch: Callable[[str], dict[str, Any]],
        market: str,
        cache_symbol: str,
        fetch_symbol: str,
    ) -> dict[str, Any] | None:
        try:
            payload = fetch(fetch_symbol)
            self.db.save_cache(market, cache_symbol, payload)
            return payload
        except Exception:
            return self.db.load_cache(market, cache_symbol)

    def _build_us_report(
        self, items: list[dict[str, Any]], errors: list[str]
    ) -> dict[str, Any]:
        item_map = {item["symbol"]: item for item in items}
        score_parts: list[float] = []
        weights = {"QQQ": 2.0, "SOXX": 2.0, "SPY": 1.2}
        for item in items:
            daily = float(item.get("daily_change_pct") or 0)
            trend = 1 if item.get("ma20") and item["close"] >= item["ma20"] else -1
            momentum = max(-3, min(3, daily)) * 7 + trend * 9
            if item["symbol"] == "^VIX":
                momentum = -momentum
            score_parts.append(momentum * weights.get(item["symbol"], 0.55))
        denominator = sum(weights.get(item["symbol"], 0.55) for item in items) or 1
        score = round(max(-100, min(100, sum(score_parts) / denominator)), 1)
        if score >= 22:
            regime, tone = "偏强", "积极但不追高"
            suggestion = "海外科技风险偏好较好。A 股开盘可重点观察科技、半导体和成长 ETF 的量价确认，分批参与，避免高开后追涨。"
        elif score <= -22:
            regime, tone = "偏弱", "防守优先"
            suggestion = "海外科技风险偏好走弱。A 股开盘前降低追涨预期，检查高波动持仓与止损线，等待指数企稳和成交量确认。"
        else:
            regime, tone = "震荡", "中性观察"
            suggestion = "海外信号分化。A 股以既定仓位和个股自身趋势为主，开盘不急于交易，等待前 30 分钟方向与量能确认。"

        leaders = sorted(
            [item for item in items if item["symbol"] != "^VIX"],
            key=lambda item: item.get("daily_change_pct") or 0,
            reverse=True,
        )
        qqq = item_map.get("QQQ", {})
        soxx = item_map.get("SOXX", {})
        vix = item_map.get("^VIX", {})
        observations = []
        if qqq:
            observations.append(
                f"纳指100单日 {qqq.get('daily_change_pct', 0):+.2f}%，20日趋势"
                f"{'向上' if qqq.get('ma20') and qqq['close'] >= qqq['ma20'] else '承压'}。"
            )
        if soxx:
            observations.append(f"半导体板块单日 {soxx.get('daily_change_pct', 0):+.2f}%。")
        if vix:
            observations.append(f"VIX 单日 {vix.get('daily_change_pct', 0):+.2f}%，当前 {vix['close']:.2f}。")
        if leaders:
            observations.append(
                f"科技股领涨为 {leaders[0]['name']}，单日 {leaders[0].get('daily_change_pct', 0):+.2f}%。"
            )
        now = datetime.now(self.timezone)
        return {
            "report_date": now.date().isoformat(),
            "generated_at": now.isoformat(timespec="seconds"),
            "market_as_of": max((item["as_of"] for item in items), default=None),
            "score": score,
            "regime": regime,
            "tone": tone,
            "suggestion": suggestion,
            "observations": observations,
            "items": items,
            "errors": errors,
            "disclaimer": "仅为基于公开行情和固定规则的决策辅助，不构成投资建议。",
        }

    def _set_status(self, **values: Any) -> None:
        with self._status_lock:
            self._status.update(values)

    def _now(self) -> str:
        return datetime.now(self.timezone).isoformat(timespec="seconds")
