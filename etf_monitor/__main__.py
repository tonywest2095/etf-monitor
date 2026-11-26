"""入口：python -m etf_monitor [--size 30] [--session auto|noon|close] [--out docs] [--demo]"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import config as C
from . import data, indicators, report, signals, sim


def _js(o):
    """numpy 标量转成 JSON 可写类型。"""
    return o.item() if hasattr(o, "item") else str(o)


def notify(text: str):
    """推送到飞书群自定义机器人。

    FEISHU_WEBHOOK：机器人地址（必填，不填就不推送）
    FEISHU_SECRET ：机器人开了"签名校验"时填它给的密钥（可选）
    机器人开了"自定义关键词"时，关键词要出现在消息里，例如填 ETF。
    """
    hook = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not hook:
        print("[info] 未设置 FEISHU_WEBHOOK，跳过飞书推送")
        return
    payload = {"msg_type": "text", "content": {"text": text}}
    secret = os.environ.get("FEISHU_SECRET", "").strip()
    if secret:
        import base64
        import hashlib
        import hmac
        import time
        ts = str(int(time.time()))
        sign = hmac.new(f"{ts}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
        payload.update(timestamp=ts, sign=base64.b64encode(sign).decode())
    try:
        import requests
        r = requests.post(hook, json=payload, timeout=10)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        code = body.get("code", body.get("StatusCode", -1 if r.status_code != 200 else 0))
        if r.status_code == 200 and code == 0:
            print("[info] 飞书推送成功")
        else:
            hint = {19021: "签名校验失败：请在 Secrets 里添加 FEISHU_SECRET，或关掉机器人的签名校验",
                    19022: "IP 不在白名单：GitHub 的服务器 IP 不固定，请关掉机器人的 IP 白名单",
                    19024: "关键词不匹配：机器人的自定义关键词请设为 ETF"}.get(code, "")
            print(f"[warn] 飞书推送失败 HTTP {r.status_code} code={code} {body.get('msg', '')} {hint}")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 飞书推送失败: {e}")


def write_archive_index(arch: Path):
    files = sorted((p.name for p in arch.glob("*.html") if p.name != "index.html"), reverse=True)
    items = "".join(f'<li><a href="{f}">{f[:-5]}</a></li>' for f in files)
    (arch / "index.html").write_text(
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>历史报告</title>'
        f'<body style="font:15px/1.8 sans-serif;padding:16px"><h1>历史报告</h1><ul>{items}</ul></body></html>',
        encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ETF 低吸高抛日报")
    ap.add_argument("--size", type=int, default=C.DEFAULT_TIER_SIZE, choices=[10, 20, 30])
    ap.add_argument("--session", default="auto", choices=["auto", "noon", "mid", "tail", "close"])
    ap.add_argument("--out", default="docs")
    ap.add_argument("--demo", action="store_true", help="用模拟行情生成演示报告（不联网）")
    ap.add_argument("--force", action="store_true", help="非交易日也照常生成")
    a = ap.parse_args(argv)

    now = data.now_bj()
    session = a.session
    out = Path(a.out)
    pool = C.pool_for(a.size)
    codes = [p["code"] for p in pool]
    need = codes + ([C.BENCHMARK["code"]] if C.BENCHMARK["code"] not in codes else [])

    raw, sources, problems = {}, {}, []
    today = now.date()
    # 先拉基准：用它判断今天有没有行情。没有就直接跳过，不去打扰其余 30 个接口。
    order = [C.BENCHMARK["code"]] + [c for c in codes if c != C.BENCHMARK["code"]]
    latest = None
    for i, code in enumerate(order):
        try:
            if a.demo:
                raw[code], sources[code] = data.demo_history(code, i), "demo"
            else:
                raw[code], sources[code] = data.fetch_history(code, out / "cache", want=latest or today)
        except data.DataError as e:
            print(f"[error] {e}")
            problems.append(f"{code} 取数失败，已从本期报告剔除")
        if i == 0:
            if code not in raw:
                print("[fatal] 基准行情取不到，不生成报告（保留上一期）")
                return 2
            latest = raw[code].index[-1].date()
            if not a.demo and latest != today and not a.force:
                print(f"[skip] 最新行情日期 {latest}，今天 {today} 不是交易日或数据未更新，跳过。")
                return 0
    got = [c for c in codes if c in raw]
    if len(got) < max(3, len(codes) // 2):
        print("[fatal] 超过一半标的取数失败，不生成报告（保留上一期）")
        return 2
    lag = [c for c in got if raw[c].index[-1].date() != latest]
    if lag:
        problems.append("以下标的行情未更新到最新交易日（可能停牌）：" + "、".join(lag))
    for src, label in (("cache", "使用了过期的本地缓存"), ("sina", "来自新浪并由程序自行复权，历史高点可能有偏差")):
        hit = [c for c in got if sources[c] == src]
        if hit:
            problems.append(f"{'、'.join(hit)} {label}")

    # 时段：行情日期不是今天（夜里或次日早上跑）一律算收盘；否则按实际时间。
    # GitHub 定时任务常晚到，11:45 的任务可能 13 点后才跑，所以不能只看是哪个定时触发的。
    intraday = a.demo or latest == today
    if session == "auto":
        hm = now.hour * 60 + now.minute
        if not intraday or hm >= 15 * 60:
            session = "close"
        elif hm < 13 * 60:
            session = "noon"
        else:
            session = "mid"
    if a.demo:
        share = C.SESSION_VOL_SHARE.get(session, 1.0)
    elif not intraday or session == "close":
        share = 1.0
    elif a.session != "auto":
        share = C.SESSION_VOL_SHARE.get(session, C.vol_share(now.hour * 60 + now.minute))
    else:
        share = C.vol_share(now.hour * 60 + now.minute)
    vol_scale = 1 / share
    frames = {c: indicators.enrich(raw[c], vol_scale) for c in raw if len(raw[c]) >= 30}
    bench = frames.get(C.BENCHMARK["code"])

    state_file = out / "state.json"
    prev = None
    if state_file.exists() and not a.demo:
        try:
            prev = json.loads(state_file.read_text(encoding="utf-8")).get("signals")
        except Exception:  # noqa: BLE001
            prev = None

    res = signals.build(pool, {c: frames[c] for c in got if c in frames}, sources, bench, prev)
    info = dict(session=session, date=str(latest), generated=now.strftime("%Y-%m-%d %H:%M"),
                demo=a.demo, problems=problems)
    html = report.render(res, info)

    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")
    arch = out / "archive"
    arch.mkdir(exist_ok=True)
    (arch / f"{latest}-{session}.html").write_text(html, encoding="utf-8")
    write_archive_index(arch)
    slim = [{k: v for k, v in r.items() if k != "spark"} for r in res["rows"]]
    (out / "latest.json").write_text(
        json.dumps(dict(info=info, temp=res["temp"], rows=slim), ensure_ascii=False, indent=1, default=_js),
        encoding="utf-8")
    if not a.demo:
        state_file.write_text(json.dumps(dict(date=str(latest), session=session, signals=res["state"]),
                                         ensure_ascii=False), encoding="utf-8")

    summary = report.text_summary(res, info, os.environ.get("REPORT_URL", ""))
    if not a.demo:
        # 模拟盘：用同一份行情自动下单、记账，生成 docs/sim.html
        try:
            sm = sim.run_live(out, pool, {c: frames[c] for c in frames}, session, str(latest), info["generated"])
            if sm and sm["equity"] is not None:
                summary += (f"\n模拟盘：总资产 {sm['equity']:,.0f}（{sm['ret']:+.2f}%，沪深300 "
                            f"{(sm['bench'] or 0):+.2f}%），持仓 {sm['n_pos']} 只，待成交 {sm['n_pending']} 笔")
        except Exception as e:  # noqa: BLE001  模拟盘出错不影响日报
            print(f"[warn] 模拟盘运行失败: {e}")
    print(summary)
    if not a.demo:
        notify(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
