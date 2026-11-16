"""行情数据层：三个免费数据源自动降级 + 本地缓存兜底 + 演示数据。

统一输出 DataFrame：index=日期，列 open/high/low/close/volume/amount，按日期升序。
价格统一为前复权（ETF 分红、拆分后历史高点才可比）。
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import config as C

BJ = ZoneInfo("Asia/Shanghai")
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}
COLS = ["open", "high", "low", "close", "volume", "amount"]


class DataError(Exception):
    pass


def now_bj() -> datetime:
    return datetime.now(BJ)


def _get(url, params=None, headers=None):
    import requests

    last = None
    for i in range(C.REQUEST_RETRIES + 1):
        try:
            r = requests.get(url, params=params, headers={**UA, **(headers or {})},
                             timeout=C.REQUEST_TIMEOUT)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.8 * (i + 1))
        finally:
            time.sleep(random.uniform(*C.REQUEST_PAUSE))
    raise DataError(f"请求失败 {url}: {last}")


def _frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date"] + COLS)
    df["date"] = pd.to_datetime(df["date"])
    for c in COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").set_index("date")
    df = df[(df["close"] > 0) & (df["high"] > 0) & (df["low"] > 0)]
    return df


# ------------------------------------------------------------------ 解析器（可单测）
def parse_eastmoney(payload: dict) -> pd.DataFrame:
    data = (payload or {}).get("data") or {}
    rows = []
    for line in data.get("klines") or []:
        p = line.split(",")
        # f51 日期, f52 开, f53 收, f54 高, f55 低, f56 成交量(手), f57 成交额
        rows.append([p[0], p[1], p[3], p[4], p[2], p[5], p[6] if len(p) > 6 else np.nan])
    return _frame(rows)


def parse_tencent(payload: dict, symbol: str) -> pd.DataFrame:
    node = ((payload or {}).get("data") or {}).get(symbol) or {}
    kl = node.get("qfqday") or node.get("day") or []
    rows = []
    for p in kl:
        # 日期, 开, 收, 高, 低, 成交量(手)[, 其他]
        rows.append([p[0], p[1], p[3], p[4], p[2], p[5], np.nan])
    return _frame(rows)


def parse_sina(payload: list) -> pd.DataFrame:
    rows = [[d["day"], d["open"], d["high"], d["low"], d["close"], d["volume"], np.nan]
            for d in (payload or [])]
    return back_adjust(_frame(rows))


def back_adjust(df: pd.DataFrame) -> pd.DataFrame:
    """新浪是不复权价。用"单日涨跌超出涨跌停可能范围"识别拆分/大额分红并向前复权。"""
    if len(df) < 2:
        return df
    ratio = (df["close"] / df["close"].shift(1)).to_numpy()
    factor = np.ones(len(df))
    f = 1.0
    for i in range(len(df) - 1, 0, -1):
        factor[i] = f
        if ratio[i] < 0.65 or ratio[i] > 1.5:
            f *= ratio[i]
    factor[0] = f
    out = df.copy()
    for c in ["open", "high", "low", "close"]:
        out[c] = out[c] * factor
    out["volume"] = out["volume"] / factor
    return out


# ------------------------------------------------------------------ 三个数据源
def fetch_eastmoney(code: str) -> pd.DataFrame:
    secid = ("1." if C.market_prefix(code) == "sh" else "0.") + code
    r = _get("https://push2his.eastmoney.com/api/qt/stock/kline/get", params=dict(
        secid=secid, ut="7eea3edcaed734bea9cbfc24409ed989",
        fields1="f1,f2,f3,f4,f5,f6", fields2="f51,f52,f53,f54,f55,f56,f57",
        klt=101, fqt=1, beg="19900101", end="20500101"))
    return parse_eastmoney(r.json())


def fetch_tencent(code: str) -> pd.DataFrame:
    sym = C.market_prefix(code) + code
    parts, end = [], ""
    for _ in range(12):  # 每页最多 2000 根，向前翻到上市日
        r = _get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                 params=dict(param=f"{sym},day,,{end},2000,qfq"))
        df = parse_tencent(r.json(), sym)
        if df.empty:
            break
        parts.append(df)
        if len(df) < 1900:
            break
        end = (df.index[0] - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    if not parts:
        return _frame([])
    df = pd.concat(parts)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df["amount"] = df["volume"] * 100 * df["close"]
    return df


def fetch_sina(code: str) -> pd.DataFrame:
    sym = C.market_prefix(code) + code
    r = _get("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
             params=dict(symbol=sym, scale=240, ma="no", datalen=1970),
             headers={"Referer": "https://finance.sina.com.cn"})
    df = parse_sina(json.loads(r.text))
    df["amount"] = df["volume"] * df["close"]
    return df


FETCHERS = {"eastmoney": fetch_eastmoney, "tencent": fetch_tencent, "sina": fetch_sina}
SOURCE_CN = {"eastmoney": "东方财富", "tencent": "腾讯", "sina": "新浪(自行复权)",
             "cache": "本地缓存(过期)", "demo": "演示数据"}


def fetch_history(code: str, cache_dir: Path | None = None, want=None):
    """返回 (df, source)。

    want: 期望行情至少更新到的日期。某个源还没更新到这天就继续试下一个源，
    都没有则返回最新的那份。全部失败时退回本地缓存；连缓存都没有才抛错。
    """
    errors, best = [], None
    for src in C.SOURCES:
        try:
            df = FETCHERS[src](code)
            if len(df) < 30:
                errors.append(f"{src}: 仅 {len(df)} 行")
                continue
            if df["amount"].isna().all():
                df["amount"] = df["volume"] * 100 * df["close"]
            if best is None or df.index[-1] > best[0].index[-1]:
                best = (df, src)
            if want is None or df.index[-1].date() >= want:
                break
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src}: {e}")
    if best is not None:
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
            best[0].to_csv(cache_dir / f"{code}.csv")
        return best
    if cache_dir and (cache_dir / f"{code}.csv").exists():
        df = pd.read_csv(cache_dir / f"{code}.csv", index_col=0, parse_dates=True)
        return df, "cache"
    raise DataError(f"{code} 所有数据源失败 -> " + " | ".join(errors))


# ------------------------------------------------------------------ 演示数据
def demo_history(code: str, idx: int, asof: str = "2026-11-13", n: int = 900) -> pd.DataFrame:
    """无网络时用的模拟行情（几何随机游走 + 人为构造的尾部形态）。绝非真实价格。"""
    rng = np.random.default_rng(int(code))
    dates = pd.bdate_range(end=asof, periods=n)
    vol = rng.uniform(0.009, 0.022)
    ret = rng.normal(rng.uniform(-0.0001, 0.0006), vol, n)
    # 给不同标的安排不同的收尾形态，保证报告里每种状态都能看到
    shape = idx % 6
    if shape == 0:      # 连续急跌 -> 深度超卖
        ret[-7:] = -np.abs(rng.normal(0.014, 0.005, 7))
    elif shape == 1:    # 底部放量三连阳 -> 右侧启动
        ret[-20:-4] = rng.normal(-0.002, 0.006, 16)
        ret[-4:] = np.abs(rng.normal(0.014, 0.004, 4))
    elif shape == 2:    # 持续拉升 -> 超买
        ret[-9:] = np.abs(rng.normal(0.009, 0.004, 9))
    elif shape == 3:    # 阴跌 -> 接近超卖
        ret[-8:] = -np.abs(rng.normal(0.006, 0.003, 8))
    close = rng.uniform(0.8, 4.0) * np.exp(np.cumsum(ret))
    spread = np.abs(rng.normal(0, vol * 0.5, n))
    high = close * (1 + spread)
    low = close * (1 - spread)
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, vol * 0.3, n))
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    volume = rng.uniform(2e5, 6e6) * np.exp(rng.normal(0, 0.25, n)) * (1 + 12 * np.abs(ret))
    if shape == 1:
        volume[-4:] *= 2.2
    df = pd.DataFrame(dict(open=open_, high=high, low=low, close=close, volume=volume),
                      index=dates)
    df.index.name = "date"
    df["amount"] = df["volume"] * 100 * df["close"]
    return df.round(4)
