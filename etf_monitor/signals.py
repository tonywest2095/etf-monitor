"""把指标翻译成"今天该看什么"：左侧低吸、右侧跟随、高抛提示、观察名单。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from .indicators import forward_stats


def _f(x, nd=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def macd_state(last, prev):
    above = last.dif > 0 and last.dea > 0
    below = last.dif < 0 and last.dea < 0
    pos = "0轴上方" if above else "0轴下方" if below else "穿越0轴"
    stronger = last.mhist > prev.mhist
    if last.gc_days > 0:
        n = int(last.gc_days)
        stage = "初期" if n <= C.GC_EARLY else "中期" if n <= C.GC_MID else "后期"
        cross = f"金叉{stage}·第{n}天"
    else:
        n = int(last.dc_days)
        stage = "初期" if n <= C.GC_EARLY else "中期" if n <= C.GC_MID else "后期"
        cross = f"死叉{stage}·第{n}天"
    return dict(pos=pos, above=above, cross=cross, golden=last.gc_days > 0, days=n, stage=stage,
                stronger=bool(stronger),
                mid_golden=bool(last.gc_days > C.GC_EARLY and last.gc_days <= C.GC_MID),
                momentum="动能增强" if stronger else "动能减弱")


def trend_pattern(last, d):
    a, b, e = last.ma5, last.ma20, last.ma60
    if np.isnan(e):
        return "数据不足"
    b_up = b > d["ma20"].iloc[-4]
    if a > b > e and b_up:
        return "多头排列"
    if a < b < e and not b_up:
        return "空头排列"
    if b_up and last.close > b:
        return "震荡偏强"
    if (not b_up) and last.close < b:
        return "震荡偏弱"
    return "均线缠绕"


def analyze(meta: dict, d: pd.DataFrame, source: str) -> dict:
    last, prev = d.iloc[-1], d.iloc[-2]
    m = macd_state(last, prev)
    above_year = None if np.isnan(last[f"ma{C.MA_LONG}"]) else bool(last.close > last[f"ma{C.MA_LONG}"])
    high_vol = bool(last.atr_pctl >= C.HIGH_VOL_PCTL) if not np.isnan(last.atr_pctl) else False
    ath_date = d["high"].idxmax()
    tail = d.tail(60)

    r = dict(
        **{k: meta[k] for k in ("code", "name", "cat", "flags")},
        source=source, date=d.index[-1].strftime("%Y-%m-%d"),
        listed=d.index[0].strftime("%Y-%m"), bars=len(d),
        close=_f(last.close, 3), chg=_f(last.chg), amount_yi=_f(last.amount / 1e8, 1),
        rsi=_f(last.rsi, 1), rsi_prev=_f(prev.rsi, 1),
        ma_dirs=[bool(last[f"ma{n}"] > prev[f"ma{n}"]) for n in C.MA_FAST],
        ma_up_days=int(last.ma_up_days), ma_dn_days=int(last.ma_dn_days),
        ma_aligned=bool(last.ma_aligned), vol_ratio=_f(last.vol_ratio),
        macd=m, dif=_f(last.dif, 4), dea=_f(last.dea, 4),
        pattern=trend_pattern(last, d), above_year=above_year,
        dd_ath=_f(last.dd_ath, 1), ath_date=ath_date.strftime("%Y-%m"), dd_60=_f(last.dd_60, 1),
        ma_fast=[_f(last[f"ma{n}"], 3) for n in C.MA_FAST],
        bias=_f(last.bias, 1), atr_pct=_f(last.atr_pct), high_vol=high_vol, vol20=_f(last.vol20, 1),
        ret_rs=_f(last.ret_rs, 1), chandelier=_f(last.chandelier, 3),
        spark=dict(close=tail["close"].round(4).tolist(),
                   **{f"ma{n}": [None if np.isnan(v) else round(v, 4) for v in tail[f"ma{n}"]]
                      for n in C.MA_TREND}),
        buy=[], sell=[], watch=[],
    )

    # ------------------------------------------------------------ 左侧：RSI 超卖
    lv = next((x for x in sorted(C.RSI_LEVELS) if last.rsi < x), None)
    if lv is not None:
        grade = {30: 1, 20: 2, 10: 3}[lv]
        turning = bool(last.rsi > prev.rsi and last.close > prev.close)
        days_below = int((d["rsi"] < 30).iloc[::-1].cummin().sum())
        scale = (1.0 if above_year or above_year is None else 0.5) * (0.5 if high_vol else 1.0)
        pos = C.MAX_POS * sum(C.LEFT_TRANCHES[:grade]) * scale * 100
        cuts = [t for t, on in (("年线下方减半", above_year is False), ("高波动减半", high_vol)) if on]
        why = [f"RSI14 = {last.rsi:.1f}，跌破 {lv}，已连续超卖 {days_below} 天",
               "今日 RSI 与价格同步回升，出现止跌迹象" if turning else "仍在下探，尚未止跌，只宜小仓试探",
               ("价格在年线上方，属于上升趋势里的回调" if above_year else
                "价格在年线下方，趋势偏弱" if above_year is False else "上市不足一年，无年线参考")]
        r["buy"].append(dict(
            kind="left", grade=grade, title=f"左侧·RSI破{lv}", turning=turning, why=why,
            days=days_below, cuts=cuts,
            pos=_f(max(pos, 1), 0), target=_f(last.ma20, 3), target_pct=_f((last.ma20 / last.close - 1) * 100, 1),
            stop_note=f"三档打满后再跌 {C.STOP_ATR_K:.0f}×ATR（约 {last.atr_pct * C.STOP_ATR_K:.1f}%），"
                      f"或 {C.FWD_DAYS} 个交易日仍站不回 MA5，认错离场",
            stats=forward_stats(d, f"trg_l{lv}"), is_new=bool(d[f"trg_l{lv}"].iloc[-1])))
    elif last.rsi < C.RSI_WATCH:
        r["watch"].append(f"RSI {last.rsi:.1f} 接近超卖线 30")

    # ------------------------------------------------------------ 右侧：三线同步向上 + 放量
    if bool(last.sig_right):
        overheat = last.bias > C.BIAS_HOT or last.rsi >= C.RSI_OVERBOUGHT[0]
        run = int(last.right_days)
        if run > C.RIGHT_FRESH_DAYS:
            r["watch"].append(f"放量上行已持续 {run} 天，持有者按止盈线 {last.chandelier:.3f} 拿，未持有不追")
        elif overheat:
            r["watch"].append(f"三线向上且放量，但已偏离 MA20 {last.bias:.1f}%、RSI {last.rsi:.0f}，不追高")
        else:
            strong = bool(last.vol_ratio >= C.VOL_RATIO_STRONG and m["above"])
            stop = last.close - C.STOP_ATR_K * last.atr
            stop_pct = (last.close - stop) / last.close
            pos = min(C.MAX_POS, C.RISK_PER_TRADE / stop_pct) * 100 * (0.5 if high_vol else 1.0)
            near_high = last.dd_60 > -3
            why = [f"MA3/MA5/MA7 同步向上第 {int(last.ma_up_days)} 天，且 MA3>MA5>MA7",
                   f"量比 {last.vol_ratio:.2f}（{'明显放量' if last.vol_ratio >= C.VOL_RATIO_STRONG else '温和放量'}）",
                   f"MACD {m['pos']}，{m['cross']}"]
            if near_high:
                why.append("贴近 60 日新高，属于突破形态")
            r["buy"].append(dict(
                kind="right", grade=2 if strong else 1, title="右侧·放量拉升" + ("（强）" if strong else ""),
                why=why, days=run, cuts=["高波动减半"] if high_vol else [],
                pos=_f(max(pos, 1), 0), stop=_f(stop, 3), stop_pct=_f(stop_pct * 100, 1),
                trail=_f(last.chandelier, 3), stats=forward_stats(d, "trg_right"),
                is_new=bool(last.trg_right)))
    elif last.ma_up_days >= C.RIGHT_MIN_DAYS and last.ma_aligned and last.rsi < C.RSI_OVERBOUGHT[0]:
        r["watch"].append(f"三线已同步向上 {int(last.ma_up_days)} 天，量比仅 {last.vol_ratio:.2f}，等放量确认")

    # ------------------------------------------------------------ 高抛：给持有者的提示
    hi, top = C.RSI_OVERBOUGHT
    if last.rsi >= top:
        r["sell"].append(dict(grade=2, title="超买", why=f"RSI14 = {last.rsi:.1f} ≥ {top}，分批止盈"))
    elif last.rsi >= hi:
        r["sell"].append(dict(grade=1, title="偏热", why=f"RSI14 = {last.rsi:.1f} ≥ {hi}，不追，持有者收紧止盈"))
    if last.ma_dn_days >= 2 and last.ret_rs > 5:
        r["sell"].append(dict(grade=1, title="三线拐头",
                              why=f"涨后 MA3/MA5/MA7 同步向下第 {int(last.ma_dn_days)} 天"))
    hot_before = d["rsi"].iloc[-10:].max() >= C.RSI_OVERBOUGHT[0] - 5   # 死叉前 10 天内 RSI 到过 65 以上
    if 1 <= last.dc_days <= C.GC_EARLY and last.dif > 0 and hot_before and last.dd_60 > -8:
        r["sell"].append(dict(grade=1, title="高位死叉",
                              why=f"涨热后 MACD 0轴上方死叉第 {int(last.dc_days)} 天"))
    if last.close < last.chandelier and prev.close >= prev.chandelier and last.ma60 > d["ma60"].iloc[-6]:
        r["sell"].append(dict(grade=2, title="破止盈线",
                              why=f"收盘跌破吊灯止盈线 {last.chandelier:.3f}，趋势单离场"))
    if m["golden"] and m["days"] <= C.GC_EARLY and not m["above"]:
        r["watch"].append(f"MACD 低位{m['cross']}")
    return r


def temperature(rows, frames):
    n = len(rows)
    above20 = sum(1 for c in frames.values() if c["close"].iloc[-1] > c["ma20"].iloc[-1]) / n * 100
    avg_rsi = float(np.mean([r["rsi"] for r in rows]))
    macd_up = sum(1 for r in rows if r["macd"]["above"]) / n * 100
    score = 0.4 * above20 + 0.4 * avg_rsi + 0.2 * macd_up
    label = ("冰点" if score < 20 else "偏冷" if score < 40 else "中性" if score < 60
             else "偏热" if score < 80 else "过热")
    hint = {"冰点": "恐慌区，左侧机会最有价值，分批不要一把梭",
            "偏冷": "情绪偏弱，耐心等超卖，右侧信号要求更高",
            "中性": "无明显偏向，按信号逐只对待",
            "偏热": "多数标的在涨，右侧顺势，但别在高位加仓",
            "过热": "普涨过热，优先考虑止盈而不是开新仓"}[label]
    cap = {"冰点": 80, "偏冷": 60, "中性": 50, "偏热": 40, "过热": 20}[label]
    return dict(score=round(score), label=label, hint=hint, cap=cap, above20=round(above20),
                avg_rsi=round(avg_rsi, 1), macd_up=round(macd_up),
                oversold=sum(1 for r in rows if r["rsi"] < 30),
                overbought=sum(1 for r in rows if r["rsi"] >= 70))


def build(pool, frames: dict, sources: dict, bench: pd.DataFrame | None, prev_state: dict | None):
    """frames: code -> 已 enrich 的 DataFrame。返回整份报告需要的全部数据。"""
    rows = [analyze(p, frames[p["code"]], sources[p["code"]]) for p in pool if p["code"] in frames]

    # RS：相对沪深300 的 20 日超额涨幅 + 池内排名
    b = float(bench["ret_rs"].iloc[-1]) if bench is not None else 0.0
    for r in rows:
        r["rs"] = _f((r["ret_rs"] or 0) - b, 1)
    for i, r in enumerate(sorted(rows, key=lambda x: -x["rs"])):
        r["rs_rank"] = i + 1

    # 相关性：同时出信号且高度同向的，只做最强的一只
    rets = pd.DataFrame({c: f["close"].pct_change().tail(C.CORR_N) for c, f in frames.items()})
    corr = rets.corr()
    name = {r["code"]: r["name"] for r in rows}
    for kind in ("left", "right"):
        cands = [r for r in rows if any(s["kind"] == kind for s in r["buy"])]
        for r in cands:
            twins = [(o["code"], corr.loc[r["code"], o["code"]]) for o in cands
                     if o is not r and corr.loc[r["code"], o["code"]] >= C.CORR_HIGH]
            sig = next(s for s in r["buy"] if s["kind"] == kind)
            sig["twins"] = [f"{name[c]}{c}(相关{v:.2f})" for c, v in twins]

    # 与上一次报告对比，标出新出现的信号
    cur_state = {r["code"]: sorted({s["title"] for s in r["buy"]} | {s["title"] for s in r["sell"]})
                 for r in rows}
    if prev_state is not None:
        for r in rows:
            old = set(prev_state.get(r["code"], []))
            for s in r["buy"] + r["sell"]:
                s["fresh"] = s["title"] not in old
    gone = []
    if prev_state:
        for code, old in prev_state.items():
            for t in set(old) - set(cur_state.get(code, [])):
                if code in name:
                    gone.append(f"{name[code]}({code})·{t}")

    review = []
    cols = [("trg_right", "右侧·放量拉升")] + [(f"trg_l{lv}", f"左侧·RSI破{lv}") for lv in C.RSI_LEVELS]
    for r in rows:
        f = frames[r["code"]]
        win = f.iloc[-(C.REVIEW_DAYS + 1):-1]
        for col, title in cols:
            for dt in win.index[win[col]]:
                p0 = float(f.loc[dt, "close"])
                review.append(dict(date=dt.strftime("%m-%d"), name=r["name"], code=r["code"], title=title,
                                   kind="right" if col == "trg_right" else "left",
                                   ret=_f((f["close"].iloc[-1] / p0 - 1) * 100, 1),
                                   days=int(len(f) - 1 - f.index.get_loc(dt))))
    review.sort(key=lambda x: x["days"])
    by_rs = sorted(rows, key=lambda x: x["rs_rank"])
    return dict(rows=rows, temp=temperature(rows, frames), state=cur_state, gone=gone,
                bench_ret=_f(b, 1), review=review,
                strong=[f'{x["name"]}({x["code"]})' for x in by_rs[:3]],
                weak=[f'{x["name"]}({x["code"]})' for x in by_rs[-3:][::-1]])
