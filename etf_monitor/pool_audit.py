"""池子复核：python -m etf_monitor.pool_audit

同一赛道列出几只候选，比较近 20 日日均成交额和最新规模，确认池里留的是不是最大最活跃的那只。
建议每月跑一次；结果只打印，不会自动改 config.py。需要联网。
"""
from __future__ import annotations

from . import config as C
from . import data

# 赛道 -> 候选（第一只是当前池内选择）
PEERS = {
    "上证指数": ["510210", "510760"],
    "中证2000": ["563300", "159531", "159532"],
    "红利": ["510880", "512890", "515180"],
    "黄金": ["518880", "159937", "518800"],
    "纳指": ["513100", "159941", "513300", "159501"],
    "芯片半导体": ["159995", "512480", "588200", "512760"],
    "证券": ["512880", "512000"],
    "通信": ["515880", "159583"],
    "医药": ["512010", "512170", "159992"],
    "消费": ["159928", "512690"],
    "日经": ["513880", "513520", "159866"],
    "德国": ["513030", "159561"],
    "军工": ["512660", "512710"],
    "银行": ["512800", "515290"],
    "有色": ["512400", "159871"],
    "机器人": ["562500", "159530", "159770"],
    "恒生科技": ["513180", "513130", "159740"],
    "光伏新能源": ["515790", "516160", "159755"],
    "煤炭": ["515220"],
    "沪深300": ["510300", "510310", "159919"],
    "标普500": ["513500", "159612"],
    "房地产": ["512200", "159768"],
    "化工": ["159870", "516020"],
    "游戏传媒": ["159869", "512980"],
    "电力": ["159611", "561560"],
    "港股创新药": ["513120", "159567"],
    "人工智能": ["159819", "515070", "515980"],
}


def sizes(codes):
    """东财批量行情里的总市值（≈ 规模，亿元）。取不到就返回空字典。"""
    try:
        secids = ",".join(("1." if C.market_prefix(c) == "sh" else "0.") + c for c in codes)
        r = data._get("https://push2.eastmoney.com/api/qt/ulist.np/get",
                      params=dict(fltt=2, fields="f12,f14,f20", secids=secids))
        return {d["f12"]: (d.get("f14"), (d.get("f20") or 0) / 1e8) for d in r.json()["data"]["diff"]}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 规模接口不可用，只比较成交额：{e}")
        return {}


def main():
    all_codes = sorted({c for v in PEERS.values() for c in v})
    sz = sizes(all_codes)
    for track, codes in PEERS.items():
        rows = []
        for c in codes:
            try:
                df, _ = data.fetch_history(c)
                amt = df["amount"].tail(20).mean() / 1e8
            except data.DataError:
                amt = float("nan")
            name, size = sz.get(c, ("", float("nan")))
            rows.append((c, name, size, amt))
        best = max(rows, key=lambda x: (x[2] if x[2] == x[2] else 0, x[3] if x[3] == x[3] else 0))
        flag = "" if best[0] == codes[0] else f"  <-- 建议换成 {best[0]}"
        print(f"\n【{track}】{flag}")
        for c, name, size, amt in rows:
            mark = "*" if c == codes[0] else " "
            print(f" {mark} {c} {name:<14} 规模 {size:8.1f} 亿   20日均成交 {amt:7.2f} 亿")


if __name__ == "__main__":
    main()
