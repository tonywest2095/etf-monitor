"""ETF 池与全部可调参数。改池子、改阈值只动这一个文件。"""

# ---------------------------------------------------------------------------
# ETF 池：tier=1 为 10 只核心档，tier<=2 为 20 只档，tier<=3 为 30 只档（上限）
# cat    : 宽基 / 微盘 / 红利 / 商品 / 海外 / 行业
# flags  : qdii  = 跨境基金，场内价可能相对净值溢价，追高前先看溢价率
#          fut   = 持有商品期货合约，有展期（升贴水）损耗，不宜长期死拿
#          t0    = 支持当日回转交易（T+0）
#          hk    = 港股标的，受港股与汇率影响
# 同类只保留一只：规模大、成交活跃、持仓与名称相符。每月用 pool_audit.py 复核一次。
# ---------------------------------------------------------------------------
POOL = [
    # ---- 第一档：10 只核心 ----
    dict(code="510210", name="上证指数ETF", cat="宽基", tier=1, flags=[]),
    dict(code="563300", name="中证2000ETF", cat="微盘", tier=1, flags=[]),
    dict(code="510880", name="红利ETF", cat="红利", tier=1, flags=[]),
    dict(code="518880", name="黄金ETF", cat="商品", tier=1, flags=["t0"]),
    dict(code="513100", name="纳指ETF", cat="海外", tier=1, flags=["qdii", "t0"]),
    dict(code="159995", name="芯片ETF", cat="行业", tier=1, flags=[]),
    dict(code="512880", name="证券ETF", cat="行业", tier=1, flags=[]),
    dict(code="515880", name="通信ETF", cat="行业", tier=1, flags=[]),
    dict(code="512010", name="医药ETF", cat="行业", tier=1, flags=[]),
    dict(code="159928", name="消费ETF", cat="行业", tier=1, flags=[]),
    # ---- 第二档：扩到 20 只 ----
    dict(code="513880", name="日经225ETF", cat="海外", tier=2, flags=["qdii", "t0"]),
    dict(code="513030", name="德国ETF", cat="海外", tier=2, flags=["qdii", "t0"]),
    dict(code="512660", name="军工ETF", cat="行业", tier=2, flags=[]),
    dict(code="512800", name="银行ETF", cat="行业", tier=2, flags=[]),
    dict(code="512400", name="有色金属ETF", cat="行业", tier=2, flags=[]),
    dict(code="562500", name="机器人ETF", cat="行业", tier=2, flags=[]),
    dict(code="159985", name="豆粕ETF", cat="商品", tier=2, flags=["fut", "t0"]),
    dict(code="513180", name="恒生科技ETF", cat="海外", tier=2, flags=["hk", "t0"]),
    dict(code="515790", name="光伏ETF", cat="行业", tier=2, flags=[]),
    dict(code="515220", name="煤炭ETF", cat="行业", tier=2, flags=[]),
    # ---- 第三档：扩到 30 只（上限） ----
    dict(code="510300", name="沪深300ETF", cat="宽基", tier=3, flags=[]),
    dict(code="513500", name="标普500ETF", cat="海外", tier=3, flags=["qdii", "t0"]),
    dict(code="159980", name="有色期货ETF", cat="商品", tier=3, flags=["fut", "t0"]),
    dict(code="159981", name="能源化工ETF", cat="商品", tier=3, flags=["fut", "t0"]),
    dict(code="512200", name="房地产ETF", cat="行业", tier=3, flags=[]),
    dict(code="159870", name="化工ETF", cat="行业", tier=3, flags=[]),
    dict(code="159869", name="游戏ETF", cat="行业", tier=3, flags=[]),
    dict(code="159611", name="电力ETF", cat="行业", tier=3, flags=[]),
    dict(code="513120", name="港股创新药ETF", cat="海外", tier=3, flags=["hk", "t0"]),
    dict(code="159819", name="人工智能ETF", cat="行业", tier=3, flags=[]),
]

# 默认档位：10 / 20 / 30
DEFAULT_TIER_SIZE = 30

# RS（相对强弱）的比较基准。不在所选档位里时也会被单独拉取，只做基准不进报告。
BENCHMARK = dict(code="510300", name="沪深300ETF")

CAT_ORDER = ["宽基", "微盘", "红利", "商品", "海外", "行业"]

# ---------------------------------------------------------------------------
# 指标参数
# ---------------------------------------------------------------------------
RSI_N = 14
RSI_LEVELS = (30, 20, 10)       # 左侧三档
RSI_WATCH = 35                  # 30~35 为"接近超卖"观察区
RSI_OVERBOUGHT = (70, 80)       # 高抛两档

MA_FAST = (3, 5, 7)             # 右侧三线
MA_TREND = (5, 20, 60)          # 均线图形
MA_LONG = 250                   # 年线，用来区分"上升趋势里的回调"与"下跌趋势里的飞刀"

VOL_RATIO_OK = 1.2              # 量比 >= 1.2 视为温和放量
VOL_RATIO_STRONG = 1.5          # 量比 >= 1.5 视为明显放量
RIGHT_MIN_DAYS = 2              # 三线同步向上至少连续 N 天
RIGHT_FRESH_DAYS = 3            # 右侧信号只在启动后 N 天内算"可上车"，之后算趋势延续、不追
# 盘中成交量折算：A股成交呈"两头高中间低"，上午约占全天 56%，14:50 前约占 93%
SESSION_VOL_SHARE = {"noon": 0.56, "tail": 0.93, "close": 1.0}


def vol_share(hm: int) -> float:
    """盘中某时刻（距 0 点的分钟数）已成交量约占全天的比例，按 U 形分布粗略估计。"""
    pts = [(9 * 60 + 30, 0.0), (10 * 60 + 30, 0.36), (11 * 60 + 30, 0.56),
           (13 * 60, 0.56), (14 * 60, 0.72), (14 * 60 + 50, 0.93), (15 * 60, 1.0)]
    if hm <= pts[0][0]:
        return 0.05
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if hm <= t1:
            return max(0.05, v0 + (v1 - v0) * (hm - t0) / max(t1 - t0, 1))
    return 1.0


REVIEW_DAYS = 20                # 报告里回顾最近 N 个交易日发出过的信号

MACD = (12, 26, 9)
GC_EARLY, GC_MID = 3, 10        # 金叉 1~3 天初期，4~10 天中期，>10 天后期

ATR_N = 14
CHANDELIER_N, CHANDELIER_K = 22, 3.0   # 吊灯止盈线：22 日最高 - 3×ATR
STOP_ATR_K = 2.0                # 初始止损：收盘价 - 2×ATR
BIAS_N = 20
BIAS_HOT = 8.0                  # 20 日乖离 > 8% 提示追高风险
HIGH_VOL_PCTL = 0.8             # ATR% 处于自身近一年 80% 分位以上 = 高波动
RS_N = 20
CORR_N, CORR_HIGH = 60, 0.80    # 60 日相关性 > 0.8 视为高度同向

# ---------------------------------------------------------------------------
# 仓位纪律（展示为占总资金的百分比，不涉及具体金额）
# ---------------------------------------------------------------------------
RISK_PER_TRADE = 0.01           # 单笔最多亏掉总资金的 1%
MAX_POS = 0.20                  # 单只上限 20%
LEFT_TRANCHES = (0.3, 0.3, 0.4) # 左侧三档分批比例：破30 / 破20 / 破10

FWD_DAYS = 10                   # 历史回看：信号出现后 N 个交易日的表现

# ---------------------------------------------------------------------------
# 模拟盘（sim.py）：把上面的信号自动变成虚拟交易
# ---------------------------------------------------------------------------
SIM_CASH = 100_000              # 初始资金（元）
SIM_MAX_POS = 0.50              # 单只 ETF 最多占总资产 50%
SIM_FEE = 0.0003                # 手续费（单边，ETF 免印花税）
SIM_SLIP = 0.0005               # 滑点（单边）：买入价上浮、卖出价下浮
SIM_LOT = 100                   # 每手 100 份
SIM_MIN_ORDER = 1000            # 低于 1000 元的单子不下
SIM_DUST = 0.03                 # 减半后剩余不到总资产 3% 的，直接清掉
SIM_LIMIT = 0.10                # 涨跌停幅度：开盘涨停买不进、跌停卖不出
SIM_BACKTEST_YEARS = 2          # 同一套规则的历史回测年数

# ---------------------------------------------------------------------------
# 数据源顺序（前一个失败自动换下一个）
# ---------------------------------------------------------------------------
SOURCES = ["eastmoney", "tencent", "sina"]
REQUEST_TIMEOUT = 12
REQUEST_RETRIES = 2
REQUEST_PAUSE = (0.4, 1.0)      # 每次请求后的随机停顿（秒），避免被限频


def pool_for(size: int):
    """按档位返回池子：10 -> tier1；20 -> tier1+2；30 -> 全部。"""
    max_tier = 1 if size <= 10 else 2 if size <= 20 else 3
    return [p for p in POOL if p["tier"] <= max_tier]


def market_prefix(code: str) -> str:
    return "sh" if code[0] in "56" else "sz"
