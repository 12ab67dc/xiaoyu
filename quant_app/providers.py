from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuantDecisionDesk/0.1"


def normalize_cn_symbol(symbol: str) -> str:
    value = symbol.strip().lower().replace(".", "")
    if value.startswith(("sh", "sz")) and len(value) == 8:
        return value
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) != 6:
        raise ValueError("A 股代码应为 6 位数字，例如 510300 或 600519")
    market = "sh" if digits.startswith(("5", "6", "9")) else "sz"
    return f"{market}{digits}"


class MarketDataProvider:
    def __init__(self, timeout: float = 15):
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
        )

    def close(self) -> None:
        self.client.close()

    def fetch_us_daily(self, symbol: str, months: int = 8) -> dict[str, Any]:
        encoded = quote(symbol, safe="")
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}"
        response = self.client.get(
            url,
            params={
                "range": f"{months}mo",
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            },
        )
        response.raise_for_status()
        chart = response.json().get("chart", {})
        if chart.get("error"):
            raise RuntimeError(str(chart["error"]))
        result = (chart.get("result") or [None])[0]
        if not result:
            raise RuntimeError(f"No Yahoo data for {symbol}")
        quote_data = result["indicators"]["quote"][0]
        adjusted = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose")
        bars = []
        for index, timestamp in enumerate(result.get("timestamp", [])):
            close = adjusted[index] if adjusted and index < len(adjusted) else quote_data["close"][index]
            if close is None:
                continue
            bars.append(
                {
                    "date": datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat(),
                    "open": quote_data["open"][index],
                    "high": quote_data["high"][index],
                    "low": quote_data["low"][index],
                    "close": close,
                    "volume": quote_data["volume"][index],
                }
            )
        if len(bars) < 2:
            raise RuntimeError(f"Insufficient Yahoo data for {symbol}")
        return {
            "symbol": symbol,
            "name": result.get("meta", {}).get("longName") or symbol,
            "currency": result.get("meta", {}).get("currency", "USD"),
            "source": "Yahoo Finance",
            "bars": bars,
        }

    def fetch_cn_daily(self, symbol: str, days: int = 180) -> dict[str, Any]:
        try:
            return self._fetch_cn_eastmoney(symbol, days)
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, IndexError):
            return self._fetch_cn_tencent(symbol, days)

    def _fetch_cn_eastmoney(self, symbol: str, days: int) -> dict[str, Any]:
        normalized = normalize_cn_symbol(symbol)
        market_id = "1" if normalized.startswith("sh") else "0"
        code = normalized[2:]
        start = (date.today() - timedelta(days=max(days * 2, 365))).strftime("%Y%m%d")
        response = self.client.get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={
                "secid": f"{market_id}.{code}",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "1",
                "beg": start,
                "end": "20500101",
                "lmt": str(days),
            },
        )
        response.raise_for_status()
        data = response.json().get("data")
        if not data or not data.get("klines"):
            raise RuntimeError(f"No Eastmoney data for {normalized}")
        bars = []
        for row in data["klines"]:
            fields = row.split(",")
            bars.append(
                {
                    "date": fields[0],
                    "open": float(fields[1]),
                    "close": float(fields[2]),
                    "high": float(fields[3]),
                    "low": float(fields[4]),
                    "volume": float(fields[5]),
                }
            )
        return {
            "symbol": normalized,
            "name": data.get("name") or normalized,
            "currency": "CNY",
            "source": "东方财富",
            "bars": bars,
        }

    def _fetch_cn_tencent(self, symbol: str, days: int) -> dict[str, Any]:
        normalized = normalize_cn_symbol(symbol)
        response = self.client.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"{normalized},day,,,{days},qfq"},
        )
        response.raise_for_status()
        data = response.json().get("data", {}).get(normalized)
        if not data:
            raise RuntimeError(f"No Tencent data for {normalized}")
        rows = data.get("qfqday") or data.get("day") or []
        if len(rows) < 2:
            raise RuntimeError(f"Insufficient Tencent data for {normalized}")
        bars = [
            {
                "date": row[0],
                "open": float(row[1]),
                "close": float(row[2]),
                "high": float(row[3]),
                "low": float(row[4]),
                "volume": float(row[5]),
            }
            for row in rows
        ]
        quote_data = data.get("qt", {}).get(normalized) or []
        name = quote_data[1] if len(quote_data) > 1 else normalized
        return {
            "symbol": normalized,
            "name": name,
            "currency": "CNY",
            "source": "腾讯行情（东方财富备用）",
            "bars": bars,
        }
