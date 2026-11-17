"""运行：python -m pytest -q   （不联网）"""
import numpy as np
import pandas as pd

from etf_monitor import data, indicators as ind, signals, config as C


def _loop_sma(x, n):          # 通达信 SMA(X,N,1) 的逐日递推写法，作为独立参照
    y, out = None, []
    for v in x:
        if np.isnan(v):
            out.append(np.nan); continue
        y = v if y is None else (v + (n - 1) * y) / n
        out.append(y)
    return np.array(out)


def test_rsi_matches_tdx_formula():
    rng = np.random.default_rng(1)
    c = pd.Series(10 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))))
    d = c.diff().to_numpy()
    up, ab = _loop_sma(np.clip(d, 0, None), 14), _loop_sma(np.abs(d), 14)
    ref = up / ab * 100
    got = ind.rsi(c, 14).to_numpy()
    assert np.allclose(got[60:], ref[60:], atol=1e-6)
    assert ind.rsi(pd.Series(np.arange(1, 60.0)), 14).iloc[-1] == 100


def test_macd_matches_loop():
    rng = np.random.default_rng(2)
    c = pd.Series(5 * np.exp(np.cumsum(rng.normal(0, 0.015, 300))))
    def ema(x, n):
        y, out = x[0], []
        for v in x:
            y = (2 * v + (n - 1) * y) / (n + 1); out.append(y)
        return np.array(out)
    dif = ema(c.to_numpy(), 12) - ema(c.to_numpy(), 26)
    dea = ema(dif, 9)
    a, b, h = ind.macd(c)
    assert np.allclose(a, dif) and np.allclose(b, dea) and np.allclose(h, 2 * (dif - dea))


def test_parsers():
    em = {"data": {"klines": ["2026-11-12,1.000,1.020,1.030,0.990,1000,102000.0",
                              "2026-11-13,1.020,1.010,1.025,1.000,1200,121000.0"]}}
    df = data.parse_eastmoney(em)
    assert list(df.iloc[-1][["open", "high", "low", "close", "volume"]]) == [1.02, 1.025, 1.0, 1.01, 1200]
    tx = {"data": {"sh510300": {"qfqday": [["2026-11-12", "1.000", "1.020", "1.030", "0.990", "1000.000"],
                                            ["2026-11-13", "1.020", "1.010", "1.025", "1.000", "1200.000", {}]]}}}
    df2 = data.parse_tencent(tx, "sh510300")
    assert df2["close"].tolist() == [1.02, 1.01] and df2["high"].iloc[0] == 1.03
    assert len(data.parse_tencent({"data": {"sh510300": {"day": tx["data"]["sh510300"]["qfqday"]}}}, "sh510300")) == 2


def test_back_adjust_handles_split():
    idx = pd.bdate_range("2026-01-01", periods=6)
    raw = pd.DataFrame(dict(open=[4, 4, 4, 2, 2, 2.1], high=[4, 4, 4, 2, 2, 2.1], low=[4, 4, 4, 2, 2, 2.1],
                            close=[4.0, 4.0, 4.0, 2.0, 2.0, 2.1], volume=[100] * 6, amount=np.nan), index=idx)
    adj = data.back_adjust(raw)
    assert np.allclose(adj["close"], [2, 2, 2, 2, 2, 2.1])     # 1拆2 之后历史高点不再虚高
    assert adj["volume"].iloc[0] == 200


def test_signals_on_constructed_shapes():
    base = data.demo_history("510300", 5)
    # 连续急跌 -> 必须出左侧信号
    crash = base.copy()
    f = np.cumprod(np.r_[np.ones(len(crash) - 10), np.full(10, 0.975)])
    for c in ["open", "high", "low", "close"]:
        crash[c] *= f
    r = signals.analyze(dict(code="x", name="x", cat="行业", flags=[]), ind.enrich(crash), "demo")
    assert r["rsi"] < 30 and r["buy"] and r["buy"][0]["kind"] == "left"
    assert r["dd_60"] < -15 and r["dd_ath"] <= r["dd_60"] + 1e-9
    # 回撤口径：距历史高点一定不小于距 60 日高点的跌幅
    e = ind.enrich(base)
    assert (e["dd_ath"] <= e["dd_60"] + 1e-9).iloc[60:].all()


def test_noon_volume_scaling():
    base = data.demo_history("510300", 5)
    full = ind.enrich(base)["vol_ratio"].iloc[-1]
    half = base.copy(); half.iloc[-1, half.columns.get_loc("volume")] *= C.SESSION_VOL_SHARE["noon"]
    noon = ind.enrich(half, 1 / C.SESSION_VOL_SHARE["noon"])["vol_ratio"].iloc[-1]
    assert abs(full - noon) < 1e-9


def test_pool_tiers():
    assert len(C.pool_for(10)) == 10 and len(C.pool_for(20)) == 20 and len(C.pool_for(30)) == 30
    codes = [p["code"] for p in C.POOL]
    assert len(set(codes)) == len(codes) and all(len(c) == 6 for c in codes)


def test_vol_share_monotonic():
    xs = [C.vol_share(h * 60 + m) for h, m in [(9, 45), (10, 30), (11, 30), (12, 30), (13, 30), (14, 50), (15, 0)]]
    assert xs == sorted(xs) and abs(xs[2] - 0.56) < 1e-9 and xs[-1] == 1.0
