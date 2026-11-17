"""模拟盘测试：python -m pytest -q（不联网）"""
import json

import pandas as pd

from etf_monitor import config as C, data, indicators as ind, sim


def _pool(n=6):
    return C.pool_for(10)[:n]


def _frames(pool, upto=None):
    out = {}
    for i, p in enumerate(pool):
        d = data.demo_history(p["code"], i)
        out[p["code"]] = ind.enrich(d if upto is None else d.iloc[:upto])
    if C.BENCHMARK["code"] not in out:
        out[C.BENCHMARK["code"]] = ind.enrich(data.demo_history(C.BENCHMARK["code"], 99)
                                              .iloc[:upto] if upto else data.demo_history(C.BENCHMARK["code"], 99))
    return out


def test_buy_fills_next_open_with_slippage_and_lots():
    st = sim.new_state("2026-01-05")
    st["pending"] = [dict(code="510880", side="buy", amount=10_000, prio=1, date="2026-01-05", reason="t",
                          meta=dict(kind="left", grade=1, stop=None, title="左侧·RSI破30"))]
    bars = {"510880": dict(date="2026-01-05", open=3.0, prev_close=3.0)}
    sim.fill(st, bars, {}, "2026-01-05")
    assert st["pending"] and not st["positions"]          # 同一天不成交
    bars = {"510880": dict(date="2026-01-06", open=3.0, prev_close=3.0)}
    sim.fill(st, bars, {}, "2026-01-06")
    p = st["positions"]["510880"]
    assert p["shares"] % C.SIM_LOT == 0 and p["shares"] * 3.0 <= 10_000
    px = 3.0 * (1 + C.SIM_SLIP)
    assert abs(st["cash"] - (C.SIM_CASH - p["shares"] * px * (1 + C.SIM_FEE))) < 1e-6
    assert not st["pending"]


def test_limit_up_open_cancels_buy_and_limit_down_defers_sell():
    st = sim.new_state("2026-01-05")
    st["pending"] = [dict(code="A", side="buy", amount=5000, prio=1, date="2026-01-05", reason="t",
                          meta=dict(kind="right", grade=1, stop=0.9, title="右侧·放量拉升"))]
    sim.fill(st, {"A": dict(date="2026-01-06", open=1.10, prev_close=1.0)}, {}, "2026-01-06")
    assert not st["positions"] and not st["pending"]
    st["positions"]["B"] = dict(shares=1000, cost=1000.0, invested=1000.0, realized=0.0, entry="2026-01-02",
                                took=[], trail=False, last_px=1.0, kind="right", grade=1, stop=0.9,
                                title="右侧·放量拉升")
    st["pending"] = [dict(code="B", side="sell", shares="all", reason="止损", date="2026-01-05")]
    sim.fill(st, {"B": dict(date="2026-01-06", open=0.90, prev_close=1.0)}, {}, "2026-01-06")
    assert st["positions"]["B"]["shares"] == 1000 and len(st["pending"]) == 1


def test_sell_records_closed_trade():
    st = sim.new_state("2026-01-05")
    st["positions"]["B"] = dict(shares=1000, cost=1000.0, invested=1000.0, realized=0.0, entry="2026-01-02",
                                took=[], trail=False, last_px=1.0, kind="left", grade=1, stop=None,
                                title="左侧·RSI破30")
    st["pending"] = [dict(code="B", side="sell", shares="all", reason="反抽到 MA20", date="2026-01-05")]
    sim.fill(st, {"B": dict(date="2026-01-06", open=1.1, prev_close=1.05)}, {"B": "测试"}, "2026-01-06")
    assert "B" not in st["positions"]
    c = st["closed"][0]
    assert c["pnl"] > 0 and c["title"] == "左侧·RSI破30" and c["name"] == "测试"


def test_live_noon_does_not_decide_close_decides_once(tmp_path):
    pool = _pool()
    full = _frames(pool)
    n = len(full[pool[0]["code"]])
    day0 = {c: f.iloc[:n - 1] for c, f in full.items()}
    d0 = day0[pool[0]["code"]].index[-1].strftime("%Y-%m-%d")
    d1 = full[pool[0]["code"]].index[-1].strftime("%Y-%m-%d")

    assert sim.run_live(tmp_path, pool, day0, "noon", d0, d0 + " 11:45") is None   # 午盘不开户
    sim.run_live(tmp_path, pool, day0, "close", d0, d0 + " 15:45")
    st = json.loads((tmp_path / "sim/state.json").read_text())
    assert st["last_decide"] == d0 and st["start"] == d0
    pend = len(st["pending"])
    sim.run_live(tmp_path, pool, day0, "close", d0, d0 + " 16:30")                 # 同日重跑不重复下单
    assert len(json.loads((tmp_path / "sim/state.json").read_text())["pending"]) == pend

    sim.run_live(tmp_path, pool, full, "noon", d1, d1 + " 11:45")
    st = json.loads((tmp_path / "sim/state.json").read_text())
    assert st["last_decide"] == d0                                                 # 午盘不做决策
    assert all(t["date"] == d1 for t in st["trades"])
    assert st["nav"][-1]["intraday"] is True
    assert (tmp_path / "sim.html").exists()


def test_backtest_accounting_is_consistent():
    pool = _pool(10)
    frames = _frames(pool)
    names = {p["code"]: p["name"] for p in pool}
    bt = sim.backtest(frames, names, C.BENCHMARK["code"], years=1)
    last = {c: float(f["close"].iloc[-1]) for c, f in frames.items()}
    assert abs(sim.equity(bt, last) - bt["nav"][-1]["equity"]) < 1
    assert bt["cash"] >= -1e-6
    for p in bt["positions"].values():
        assert p["shares"] > 0 and p["shares"] % C.SIM_LOT == 0
    realized = sum(x["pnl"] for x in bt["trades"] if x["pnl"] is not None)
    assert abs(realized - sum(x["pnl"] for x in bt["closed"]) -
               sum(p["realized"] for p in bt["positions"].values())) < 1
    html = sim.render(bt, bt, last, names, "close", "2026-11-17 15:45")
    assert "历史回测" in html and "当前持仓" in html
