#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股利好监控系统
- 同花顺7x24新闻抓取 + 情绪分析
- 腾讯行情/K线数据
- 暗色主题HTML监控面板
- Telegram Bot推送 (命令监听 + 定时推送 + 利好预警)
"""

import os, sys, re, json, time, math, hashlib, traceback
from datetime import datetime, timedelta
from collections import defaultdict
import requests

# ─── 配置 ───
TG_TOKEN = os.environ.get("TG_TOKEN", "8706943976:AAFHmegbegMAYeG0-iZpyEqp69_StQl-iFQ")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "8410634590")
TG_CHANNEL = os.environ.get("TG_CHANNEL", "")
CREDITS_TOTAL = int(os.environ.get("CREDITS_TOTAL", "10000"))
CREDITS_DAILY = int(os.environ.get("CREDITS_DAILY", "500"))

API_CALL_COUNT = 0
START_TIME = time.time()

# ─── 情绪关键词 ───
BULL_KW = [
    "利好", "涨停", "大涨", "暴涨", "突破", "新高", "反弹", "拉升", "放量",
    "资金流入", "北向资金", "加仓", "增持", "回购", "分红", "业绩预增",
    "超预期", "订单", "中标", "签约", "合作", "战略", "政策支持", "补贴",
    "降准", "降息", "宽松", "刺激", "扩大内需", "利润增长", "营收增长",
    "净利润", "同比增长", "环比增长", "扭亏", "盈利", "翻倍", "产能",
    "量产", "商业化", "出口", "海外订单", "国产替代", "自主可控",
]
BEAR_KW = [
    "利空", "跌停", "大跌", "暴跌", "下跌", "新低", "破位", "杀跌", "缩量",
    "资金流出", "减持", "质押", "违规", "处罚", "亏损", "业绩下滑",
    "不及预期", "风险", "退市", "暂停", "终止", "诉讼", "调查",
]
POLICY_KW = [
    "国务院", "央行", "证监会", "发改委", "工信部", "财政部", "政治局",
    "常务会议", "国常会", "两会", "政策", "规划", "意见", "通知",
]
HOT_KW = [
    "AI", "人工智能", "芯片", "半导体", "光刻", "算力", "大模型", "机器人",
    "无人驾驶", "自动驾驶", "新能源", "锂电", "光伏", "储能", "氢能",
    "低空经济", "无人机", "卫星", "量子", "生物医药", "创新药",
]

# ─── A股市场过滤 ───
ASHARE_MARKETS = {"22", "33", "151", "17"}

# ─── 工具函数 ───
def stock_prefix(code):
    """根据股票代码判断sh/sz前缀"""
    if code.startswith(("6", "688")):
        return f"sh{code}"
    elif code.startswith(("0", "3")):
        return f"sz{code}"
    return code

def fmt_time(ts):
    """Unix时间戳转可读时间"""
    return datetime.fromtimestamp(int(ts)).strftime("%H:%M:%S")

def fmt_date(ts):
    """Unix时间戳转日期"""
    return datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M")

def age_hours(ts):
    """计算新闻距今小时数"""
    return (time.time() - int(ts)) / 3600

def recency_factor(ts):
    """时效衰减因子"""
    return 1.0 / (1.0 + age_hours(ts) * 0.15)

def impact_level(score):
    """影响等级: S/A/B/C"""
    if score >= 4:
        return "S"
    elif score >= 2.5:
        return "A"
    elif score >= 1.5:
        return "B"
    return "C"

def impact_emoji(level):
    """等级对应标识"""
    return {"S": "🔴", "A": "🟠", "B": "🟡", "C": "⚪"}.get(level, "⚪")

def safe_request(url, params=None, timeout=10, retries=2):
    """带重试的HTTP请求"""
    global API_CALL_COUNT
    for i in range(retries):
        try:
            API_CALL_COUNT += 1
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r
        except Exception as e:
            if i == retries - 1:
                print(f"[WARN] 请求失败 {url}: {e}")
                return None
            time.sleep(0.5)
    return None

# ─── 同花顺新闻抓取 ───
THS_API = "https://news.10jqka.com.cn/tapp/news/push/stock/"

def fetch_ths_news():
    """从同花顺3个频道各抓4页新闻, 去重"""
    channels = [
        ("", "全部"),
        ("-21101", "重要"),
        ("21109", "机会"),
    ]
    all_news = {}
    for tag, label in channels:
        for page in range(1, 5):
            url = THS_API
            params = {"page": page, "tag": tag, "track": "website", "pagesize": 30}
            r = safe_request(url, params=params)
            if not r:
                continue
            try:
                data = r.json()
                items = data.get("data", {}).get("list", [])
                for item in items:
                    nid = str(item.get("id", ""))
                    if nid and nid not in all_news:
                        item["_channel"] = label
                        all_news[nid] = item
            except Exception as e:
                print(f"[WARN] 解析THS {label} p{page}: {e}")
            time.sleep(0.2)
    print(f"[INFO] 抓取新闻 {len(all_news)} 条 (去重后)")
    return list(all_news.values())

# ─── 情绪分析 ───
STOCK_CODE_RE = re.compile(r"(?:sh|sz|SH|SZ)?(\d{6})")
SECTOR_KW = [
    "板块", "概念", "行业", "指数", "ETF", "基金",
    "半导体", "芯片", "AI", "人工智能", "新能源", "光伏", "锂电", "储能",
    "军工", "医药", "消费", "地产", "银行", "券商", "保险", "煤炭",
    "有色", "钢铁", "化工", "汽车", "白酒", "旅游", "传媒", "通信",
    "电力", "农业", "机器人", "无人机", "低空经济", "卫星", "量子",
]
ETF_MAP = {
    "半导体": "512480", "芯片": "159995", "AI": "515070", "人工智能": "515070",
    "新能源": "516160", "光伏": "515790", "锂电": "159840", "储能": "159566",
    "军工": "512660", "医药": "512010", "消费": "159928", "券商": "512000",
    "银行": "512800", "地产": "159768", "白酒": "512690", "机器人": "562500",
    "传媒": "512980", "通信": "515880", "电力": "159611", "有色": "512400",
}

def analyze_sentiment(news_list):
    """对新闻列表做情绪分析, 提取股票/板块/ETF"""
    results = []
    stock_mentions = defaultdict(lambda: {"count": 0, "score": 0, "names": set(), "news": []})
    sector_mentions = defaultdict(lambda: {"count": 0, "score": 0, "news": []})

    for item in news_list:
        title = item.get("title", "") or ""
        content = item.get("digest", "") or item.get("content", "") or ""
        text = title + " " + content
        ctime = item.get("ctime", 0)
        importance = float(item.get("importance", 0) or 0)

        # 情绪评分
        bull = sum(1 for kw in BULL_KW if kw in text)
        bear = sum(1 for kw in BEAR_KW if kw in text)
        policy = sum(0.5 for kw in POLICY_KW if kw in text)
        hot = sum(0.3 for kw in HOT_KW if kw in text)
        raw_score = bull - bear * 1.5 + policy + hot + importance * 0.5
        level = impact_level(raw_score)

        # 提取股票代码 (THS API字段为 stock 或 stockInfo)
        stocks_in = []
        stock_info = item.get("stock", []) or item.get("stockInfo", []) or []
        for si in stock_info:
            code = si.get("stockCode", "")
            name = si.get("name", "") or si.get("stockName", "")
            market = str(si.get("stockMarket", ""))
            # 过滤: 只保留A股 (纯数字6位代码 + 市场在A股范围)
            if code and market in ASHARE_MARKETS and re.match(r'^\d{6}$', code):
                stocks_in.append({"code": code, "name": name})
                stock_mentions[code]["count"] += 1
                stock_mentions[code]["score"] += raw_score
                stock_mentions[code]["names"].add(name)
                stock_mentions[code]["news"].append({"title": title, "time": ctime})

        # 提取板块
        sectors_in = []
        for skw in SECTOR_KW:
            if skw in text:
                sectors_in.append(skw)
                sector_mentions[skw]["count"] += 1
                sector_mentions[skw]["score"] += raw_score
                sector_mentions[skw]["news"].append({"title": title, "time": ctime})

        results.append({
            "id": item.get("id", ""),
            "title": title,
            "digest": content[:120],
            "ctime": int(ctime) if ctime else int(time.time()),
            "channel": item.get("_channel", ""),
            "score": round(raw_score, 2),
            "level": level,
            "stocks": stocks_in,
            "sectors": sectors_in,
            "bull_count": bull,
            "bear_count": bear,
        })

    # 排序: 利好分数 * 时效
    results.sort(key=lambda x: x["score"] * recency_factor(x["ctime"]), reverse=True)

    # Hot7: 被提及最多且分数最高的股票
    hot7 = sorted(stock_mentions.items(),
                  key=lambda x: x[1]["count"] * 0.4 + x[1]["score"] * 0.6,
                  reverse=True)[:7]
    hot7_list = []
    for code, info in hot7:
        hot7_list.append({
            "code": code,
            "name": list(info["names"])[0] if info["names"] else code,
            "count": info["count"],
            "score": round(info["score"], 2),
            "news": info["news"][:3],
        })

    # 板块排名
    sector_rank = sorted(sector_mentions.items(),
                         key=lambda x: x[1]["count"] * 0.3 + x[1]["score"] * 0.7,
                         reverse=True)[:10]
    sector_list = []
    for name, info in sector_rank:
        etf_code = ETF_MAP.get(name, "")
        sector_list.append({
            "name": name,
            "count": info["count"],
            "score": round(info["score"], 2),
            "etf": etf_code,
        })

    return results, hot7_list, sector_list

# ─── 腾讯行情 ───
TENCENT_QUOTE_API = "https://qt.gtimg.cn/q="
TENCENT_KLINE_API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

def fetch_tencent_quotes(codes):
    """批量获取腾讯实时行情, codes为带前缀的列表如['sh600519']"""
    if not codes:
        return {}
    results = {}
    # 每批最多30个
    for i in range(0, len(codes), 30):
        batch = codes[i:i+30]
        symbols = ",".join(batch)
        r = safe_request(TENCENT_QUOTE_API + symbols)
        if not r:
            continue
        text = r.text
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            try:
                var_part, val_part = line.split("=", 1)
                sym = var_part.split("_")[-1].strip()
                val = val_part.strip().strip(";").strip('"')
                fields = val.split("~")
                if len(fields) < 45:
                    continue
                results[sym] = {
                    "name": fields[1],
                    "code": fields[2],
                    "price": float(fields[3]) if fields[3] else 0,
                    "prev_close": float(fields[4]) if fields[4] else 0,
                    "open": float(fields[5]) if fields[5] else 0,
                    "volume": float(fields[6]) if fields[6] else 0,  # 手
                    "change": float(fields[31]) if fields[31] else 0,
                    "change_pct": float(fields[32]) if fields[32] else 0,
                    "high": float(fields[33]) if fields[33] else 0,
                    "low": float(fields[34]) if fields[34] else 0,
                    "amount": float(fields[37]) if fields[37] else 0,  # 万元
                    "turnover": float(fields[38]) if fields[38] else 0,
                    "pe": float(fields[39]) if fields[39] else 0,
                    "market_cap": float(fields[44]) if fields[44] else 0,  # 亿
                }
            except Exception:
                continue
        time.sleep(0.2)
    return results

def fetch_kline(symbol, days=30):
    """获取K线数据 (前复权日线)"""
    params = {"param": f"{symbol},day,,,{days},qfq", "_var": "kline_dayqfq"}
    r = safe_request(TENCENT_KLINE_API, params=params)
    if not r:
        return []
    try:
        text = r.text
        # 提取JSON部分
        idx = text.index("{")
        data = json.loads(text[idx:].rstrip(";"))
        inner = data.get("data", {}).get(symbol, {})
        klines = inner.get("qfqday", inner.get("day", []))
        result = []
        for k in klines:
            result.append({
                "date": k[0],
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]) if len(k) > 5 else 0,
            })
        return result
    except Exception as e:
        print(f"[WARN] K线解析失败 {symbol}: {e}")
        return []

def calc_streaks(klines):
    """计算连涨天数"""
    if not klines:
        return 0
    streak = 0
    for k in reversed(klines):
        if k["close"] >= k["open"]:
            streak += 1
        else:
            break
    return streak

def build_realtime_picks(news_results, hot7, quotes, streaks):
    """构建实时利好精选, 综合评分排序"""
    picks = []
    seen_codes = set()
    for item in news_results:
        if item["score"] < 1.5:
            continue
        for stock in item["stocks"]:
            code = stock["code"]
            if code in seen_codes:
                continue
            seen_codes.add(code)
            sym = stock_prefix(code)
            q = quotes.get(sym, {})
            streak = streaks.get(sym, 0)
            elasticity = q.get("change_pct", 0)
            combined = item["score"] * recency_factor(item["ctime"]) * (1 + abs(elasticity) * 0.1)
            picks.append({
                "code": code,
                "name": stock.get("name", q.get("name", code)),
                "price": q.get("price", 0),
                "change_pct": q.get("change_pct", 0),
                "streak": streak,
                "news_score": item["score"],
                "combined": round(combined, 2),
                "level": impact_level(combined),
                "title": item["title"],
                "ctime": item["ctime"],
                "volume": q.get("volume", 0),
                "amount": q.get("amount", 0),
            })
    picks.sort(key=lambda x: x["combined"], reverse=True)
    return picks[:20]

# ─── HTML 面板生成 ───
def generate_html(news_results, hot7, picks, sector_list, quotes, streaks):
    """生成暗色主题HTML监控面板"""
    elapsed = round(time.time() - START_TIME, 1)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    credits_used = API_CALL_COUNT
    credits_remain = max(0, CREDITS_DAILY - credits_used)

    # 准备JSON数据
    hot7_json = json.dumps(hot7, ensure_ascii=False)
    picks_json = json.dumps(picks[:20], ensure_ascii=False)
    sectors_json = json.dumps(sector_list, ensure_ascii=False)
    news_json = json.dumps(news_results[:60], ensure_ascii=False)

    # 为Hot7准备K线 (简化: 用quotes中的涨跌)
    hot7_quotes = []
    for h in hot7:
        sym = stock_prefix(h["code"])
        q = quotes.get(sym, {})
        hot7_quotes.append({
            "code": h["code"],
            "name": h["name"],
            "price": q.get("price", 0),
            "change_pct": q.get("change_pct", 0),
            "count": h["count"],
            "score": h["score"],
            "streak": streaks.get(sym, 0),
        })
    hot7_quotes_json = json.dumps(hot7_quotes, ensure_ascii=False)

    html = HTML_TEMPLATE
    html = html.replace("__NOW__", now_str)
    html = html.replace("__ELAPSED__", str(elapsed))
    html = html.replace("__API_CALLS__", str(API_CALL_COUNT))
    html = html.replace("__CREDITS_REMAIN__", str(credits_remain))
    html = html.replace("__CREDITS_DAILY__", str(CREDITS_DAILY))
    html = html.replace("__CREDITS_TOTAL__", str(CREDITS_TOTAL))
    html = html.replace("__HOT7_JSON__", hot7_quotes_json)
    html = html.replace("__PICKS_JSON__", picks_json)
    html = html.replace("__SECTORS_JSON__", sectors_json)
    html = html.replace("__NEWS_JSON__", news_json)
    html = html.replace("__NEWS_COUNT__", str(len(news_results)))
    html = html.replace("__BULL_COUNT__", str(sum(1 for n in news_results if n["score"] > 0)))
    html = html.replace("__HOT7_COUNT__", str(len(hot7)))
    html = html.replace("__PICKS_COUNT__", str(len(picks)))

    return html


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股利好监控面板</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{--bg-primary:#0a0e17;--bg-card:#111827;--bg-card-hover:#1a2332;--border:#1e293b;--text-primary:#e2e8f0;--text-secondary:#94a3b8;--text-muted:#64748b;--red:#ef4444;--red-glow:#ef444440;--green:#22c55e;--green-glow:#22c55e40;--gold:#f59e0b;--blue:#3b82f6;--purple:#8b5cf6;--accent:#06b6d4}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Noto Sans SC','JetBrains Mono',sans-serif;background:var(--bg-primary);color:var(--text-primary);min-height:100vh;overflow-x:hidden}
.mono{font-family:'JetBrains Mono',monospace}
.card{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;transition:all .2s}
.card:hover{background:var(--bg-card-hover);border-color:#334155}
.glow-red{box-shadow:0 0 20px var(--red-glow)}
.glow-green{box-shadow:0 0 20px var(--green-glow)}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500}
.tag-s{background:#ef444430;color:#ef4444;border:1px solid #ef444450}
.tag-a{background:#f59e0b30;color:#f59e0b;border:1px solid #f59e0b50}
.tag-b{background:#3b82f630;color:#3b82f6;border:1px solid #3b82f650}
.tag-c{background:#64748b30;color:#94a3b8;border:1px solid #64748b50}
.scrollbar::-webkit-scrollbar{width:4px}
.scrollbar::-webkit-scrollbar-track{background:transparent}
.scrollbar::-webkit-scrollbar-thumb{background:#334155;border-radius:2px}
.pulse{animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.fade-in{animation:fadeIn .5s ease-out}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.topbar{background:linear-gradient(135deg,#0f172a 0%,#1e1b4b 100%);border-bottom:1px solid var(--border)}
.stat-box{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:8px;padding:12px 16px}
.hot-rank{background:linear-gradient(135deg,var(--red) 0%,#dc2626 100%);color:white;width:24px;height:24px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0}
.news-item{border-left:3px solid transparent;padding-left:12px;transition:all .2s}
.news-item:hover{border-left-color:var(--accent);background:rgba(6,182,212,0.05)}
.news-item.bull{border-left-color:var(--red)}
.news-item.bear{border-left-color:var(--green)}
</style>
<script>
tailwind.config={theme:{extend:{colors:{primary:'#0a0e17',card:'#111827'}}}}
</script>
</head>
<body>
<!-- 顶栏 -->
<div class="topbar sticky top-0 z-50 px-4 py-3">
<div class="max-w-[1600px] mx-auto flex items-center justify-between flex-wrap gap-3">
  <div class="flex items-center gap-3">
    <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-red-500 to-orange-500 flex items-center justify-center text-white font-bold text-sm">A</div>
    <div>
      <h1 class="text-lg font-bold text-white leading-tight">A股利好监控</h1>
      <p class="text-xs text-slate-400">__NOW__</p>
    </div>
    <span class="pulse w-2 h-2 rounded-full bg-green-400 ml-1"></span>
  </div>
  <div class="flex items-center gap-3 flex-wrap">
    <div class="stat-box flex items-center gap-2">
      <span class="text-xs text-slate-400">新闻</span>
      <span class="mono text-sm font-bold text-cyan-400">__NEWS_COUNT__</span>
    </div>
    <div class="stat-box flex items-center gap-2">
      <span class="text-xs text-slate-400">利好</span>
      <span class="mono text-sm font-bold text-red-400">__BULL_COUNT__</span>
    </div>
    <div class="stat-box flex items-center gap-2">
      <span class="text-xs text-slate-400">耗时</span>
      <span class="mono text-sm text-slate-300">__ELAPSED__s</span>
    </div>
    <div class="stat-box flex items-center gap-2">
      <span class="text-xs text-slate-400">API</span>
      <span class="mono text-sm text-slate-300">__API_CALLS__</span>
    </div>
    <div class="stat-box flex items-center gap-2">
      <span class="text-xs text-slate-400">Credits</span>
      <span class="mono text-sm text-amber-400">__CREDITS_REMAIN__/__CREDITS_DAILY__</span>
    </div>
  </div>
</div>
</div>

<!-- 主体 -->
<div class="max-w-[1600px] mx-auto p-4">
<div class="grid grid-cols-1 lg:grid-cols-3 gap-4">

<!-- 左列: Hot7 + 板块ETF -->
<div class="space-y-4">

  <!-- Hot7 热点股 -->
  <div class="card p-4 fade-in">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-base font-bold flex items-center gap-2">
        <span class="text-red-400">🔥</span> Hot7 热点股
      </h2>
      <span class="tag tag-s">TOP 7</span>
    </div>
    <div class="space-y-3" id="hot7-list"></div>
  </div>

  <!-- 板块 ETF -->
  <div class="card p-4 fade-in">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-base font-bold flex items-center gap-2">
        <span class="text-blue-400">📋</span> 热门板块 & ETF
      </h2>
    </div>
    <div class="space-y-2" id="sector-list"></div>
    <div id="sector-chart" style="height:220px;margin-top:12px"></div>
  </div>

</div>

<!-- 中列: 实时利好精选 -->
<div class="space-y-4">
  <div class="card p-4 fade-in">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-base font-bold flex items-center gap-2">
        <span class="text-amber-400">⚡</span> 实时利好精选
      </h2>
      <span class="mono text-xs text-slate-400">__PICKS_COUNT__ 只</span>
    </div>
    <div class="space-y-2 scrollbar overflow-y-auto" style="max-height:calc(100vh - 200px)" id="picks-list"></div>
  </div>
</div>

<!-- 右列: 新闻流 -->
<div class="space-y-4">
  <div class="card p-4 fade-in">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-base font-bold flex items-center gap-2">
        <span class="text-cyan-400">📰</span> 新闻流
      </h2>
      <div class="flex gap-2">
        <button onclick="filterNews('all')" class="tag tag-c hover:opacity-80 cursor-pointer" id="btn-all">全部</button>
        <button onclick="filterNews('bull')" class="tag tag-s hover:opacity-80 cursor-pointer" id="btn-bull">利好</button>
        <button onclick="filterNews('bear')" class="tag tag-b hover:opacity-80 cursor-pointer" id="btn-bear">利空</button>
      </div>
    </div>
    <div class="space-y-1 scrollbar overflow-y-auto" style="max-height:calc(100vh - 200px)" id="news-list"></div>
  </div>
</div>

</div>
</div>

<script>
// 数据注入
const hot7Data = __HOT7_JSON__;
const picksData = __PICKS_JSON__;
const sectorsData = __SECTORS_JSON__;
const newsData = __NEWS_JSON__;

// ─── 渲染 Hot7 ───
(function(){
  const el = document.getElementById('hot7-list');
  let html = '';
  hot7Data.forEach((h, i) => {
    const pctClass = h.change_pct >= 0 ? 'text-red-400' : 'text-green-400';
    const pctStr = h.change_pct >= 0 ? '+' + h.change_pct.toFixed(2) + '%' : h.change_pct.toFixed(2) + '%';
    const streakBadge = h.streak > 0 ? `<span class="tag tag-s ml-1">${h.streak}连阳</span>` : '';
    html += `
    <div class="flex items-center gap-3 p-2 rounded-lg hover:bg-white/5 transition-all">
      <div class="hot-rank" style="opacity:${1 - i*0.08}">${i+1}</div>
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2">
          <span class="font-bold text-sm">${h.name}</span>
          <span class="mono text-xs text-slate-500">${h.code}</span>
          ${streakBadge}
        </div>
        <div class="flex items-center gap-3 mt-1">
          <span class="mono text-sm ${pctClass}">${h.price > 0 ? h.price.toFixed(2) : '--'}</span>
          <span class="mono text-xs ${pctClass}">${pctStr}</span>
          <span class="text-xs text-slate-500">提及${h.count}次</span>
        </div>
      </div>
      <div class="text-right">
        <div class="mono text-xs text-amber-400">评分 ${h.score}</div>
      </div>
    </div>`;
  });
  el.innerHTML = html || '<div class="text-center text-slate-500 py-8">暂无数据</div>';
})();

// ─── 渲染精选 ───
(function(){
  const el = document.getElementById('picks-list');
  let html = '';
  picksData.forEach((p, i) => {
    const pctClass = p.change_pct >= 0 ? 'text-red-400' : 'text-green-400';
    const pctStr = p.change_pct >= 0 ? '+' + p.change_pct.toFixed(2) + '%' : p.change_pct.toFixed(2) + '%';
    const tagClass = 'tag-' + p.level.toLowerCase();
    const time = new Date(p.ctime * 1000);
    const timeStr = time.getHours().toString().padStart(2,'0') + ':' + time.getMinutes().toString().padStart(2,'0');
    html += `
    <div class="p-3 rounded-lg hover:bg-white/5 transition-all border border-transparent hover:border-slate-700">
      <div class="flex items-center justify-between mb-1">
        <div class="flex items-center gap-2">
          <span class="tag ${tagClass}">${p.level}</span>
          <span class="font-bold text-sm">${p.name}</span>
          <span class="mono text-xs text-slate-500">${p.code}</span>
        </div>
        <span class="mono text-xs text-slate-500">${timeStr}</span>
      </div>
      <div class="flex items-center gap-3 mb-1">
        <span class="mono text-sm font-bold ${pctClass}">${p.price > 0 ? p.price.toFixed(2) : '--'}</span>
        <span class="mono text-xs ${pctClass}">${pctStr}</span>
        ${p.streak > 0 ? `<span class="text-xs text-amber-400">${p.streak}连阳</span>` : ''}
        <span class="mono text-xs text-slate-500">评分${p.combined}</span>
      </div>
      <p class="text-xs text-slate-400 truncate">${p.title}</p>
    </div>`;
  });
  el.innerHTML = html || '<div class="text-center text-slate-500 py-8">暂无数据</div>';
})();

// ─── 渲染板块 ───
(function(){
  const el = document.getElementById('sector-list');
  let html = '';
  sectorsData.forEach((s, i) => {
    const scoreColor = s.score > 0 ? 'text-red-400' : s.score < 0 ? 'text-green-400' : 'text-slate-400';
    html += `
    <div class="flex items-center justify-between p-2 rounded hover:bg-white/5 transition-all">
      <div class="flex items-center gap-2">
        <span class="text-xs text-slate-500 mono w-5">${i+1}</span>
        <span class="text-sm font-medium">${s.name}</span>
        ${s.etf ? `<span class="mono text-xs text-cyan-400/60">${s.etf}</span>` : ''}
      </div>
      <div class="flex items-center gap-3">
        <span class="text-xs text-slate-500">x${s.count}</span>
        <span class="mono text-xs ${scoreColor}">${s.score > 0 ? '+' : ''}${s.score}</span>
      </div>
    </div>`;
  });
  el.innerHTML = html || '<div class="text-center text-slate-500 py-4">暂无数据</div>';

  // 板块图表
  if(sectorsData.length > 0) {
    const chart = echarts.init(document.getElementById('sector-chart'));
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#e2e8f0', fontSize: 12 } },
      grid: { left: 60, right: 20, top: 10, bottom: 24 },
      xAxis: { type: 'value', axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: '#64748b', fontSize: 10 }, splitLine: { lineStyle: { color: '#1e293b' } } },
      yAxis: { type: 'category', data: sectorsData.slice(0,8).map(s=>s.name).reverse(), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
      series: [{ type: 'bar', data: sectorsData.slice(0,8).map(s=>s.score).reverse(), barWidth: 16, itemStyle: { borderRadius: [0,4,4,0], color: function(p) { return p.data >= 0 ? new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:'#ef444480'},{offset:1,color:'#ef4444'}]) : new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:'#22c55e80'},{offset:1,color:'#22c55e'}]); } } }]
    });
    window.addEventListener('resize', () => chart.resize());
  }
})();

// ─── 渲染新闻流 ───
let currentFilter = 'all';
function renderNews(filter) {
  const el = document.getElementById('news-list');
  let filtered = newsData;
  if (filter === 'bull') filtered = newsData.filter(n => n.score > 0);
  else if (filter === 'bear') filtered = newsData.filter(n => n.score < 0);

  let html = '';
  filtered.slice(0, 50).forEach(n => {
    const cls = n.score > 1 ? 'bull' : n.score < -1 ? 'bear' : '';
    const tagClass = 'tag-' + n.level.toLowerCase();
    const time = new Date(n.ctime * 1000);
    const timeStr = time.getHours().toString().padStart(2,'0') + ':' + time.getMinutes().toString().padStart(2,'0');
    const stockTags = (n.stocks || []).slice(0,3).map(s => `<span class="text-xs text-cyan-400">${s.name}</span>`).join(' ');
    html += `
    <div class="news-item ${cls} py-2">
      <div class="flex items-center gap-2 mb-1">
        <span class="tag ${tagClass}">${n.level}</span>
        <span class="mono text-xs text-slate-500">${timeStr}</span>
        <span class="text-xs text-slate-600">${n.channel}</span>
      </div>
      <p class="text-sm leading-relaxed mb-1">${n.title}</p>
      <div class="flex items-center gap-2 flex-wrap">
        ${stockTags}
        ${n.sectors && n.sectors.length ? `<span class="text-xs text-purple-400">${n.sectors.slice(0,2).join(' ')}</span>` : ''}
      </div>
    </div>`;
  });
  el.innerHTML = html || '<div class="text-center text-slate-500 py-8">暂无数据</div>';
}
function filterNews(f) {
  currentFilter = f;
  document.querySelectorAll('[id^="btn-"]').forEach(b => { b.className = 'tag tag-c hover:opacity-80 cursor-pointer'; });
  const btnId = 'btn-' + f;
  const btn = document.getElementById(btnId);
  if(btn) btn.className = 'tag tag-s hover:opacity-80 cursor-pointer';
  renderNews(f);
}
renderNews('all');
</script>
</body>
</html>"""

# ─── Telegram 推送 ───
TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"

def send_telegram(text, chat_id=None, parse_mode="HTML"):
    """发送Telegram消息, 支持多目标, 自动分段"""
    if chat_id is None:
        targets = [TG_CHAT_ID]
        if TG_CHANNEL:
            targets.append(TG_CHANNEL)
    elif isinstance(chat_id, list):
        targets = chat_id
    else:
        targets = [str(chat_id)]

    # 自动分段 (Telegram限制4096字符)
    chunks = []
    while len(text) > 4000:
        cut = text[:4000].rfind("\n")
        if cut < 100:
            cut = 4000
        chunks.append(text[:cut])
        text = text[cut:]
    if text.strip():
        chunks.append(text)

    for target in targets:
        for chunk in chunks:
            try:
                r = requests.post(f"{TG_API}/sendMessage", json={
                    "chat_id": target,
                    "text": chunk,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                }, timeout=15)
                if not r.ok:
                    print(f"[WARN] TG发送失败 {target}: {r.text[:200]}")
            except Exception as e:
                print(f"[WARN] TG发送异常 {target}: {e}")
            time.sleep(0.3)

def format_summary_msg(hot7, picks, sector_list):
    """格式化推送摘要"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"<b>📊 A股利好监控 | {now}</b>", ""]

    # Hot7
    lines.append("<b>🔥 Hot7 热点股</b>")
    for i, h in enumerate(hot7, 1):
        lines.append(f"  {i}. <b>{h['name']}</b>({h['code']}) 提及{h['count']}次 评分{h['score']}")
    lines.append("")

    # 精选利好
    lines.append("<b>⚡ 实时利好精选</b>")
    for i, p in enumerate(picks[:7], 1):
        arrow = "📈" if p["change_pct"] >= 0 else "📉"
        pct = f"+{p['change_pct']:.2f}%" if p["change_pct"] >= 0 else f"{p['change_pct']:.2f}%"
        lines.append(
            f"  {impact_emoji(p['level'])} <b>{p['name']}</b> {p['price']}元 {arrow}{pct}"
            f" | 评分{p['combined']} | {p['title'][:25]}"
        )
    lines.append("")

    # 板块
    if sector_list:
        lines.append("<b>📋 热门板块</b>")
        for s in sector_list[:5]:
            etf_info = f" ETF:{s['etf']}" if s['etf'] else ""
            lines.append(f"  • {s['name']} 提及{s['count']}次 评分{s['score']}{etf_info}")

    return "\n".join(lines)

def get_updates(offset=None, timeout=30):
    """获取Telegram Bot更新"""
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"{TG_API}/getUpdates", params=params, timeout=timeout + 10)
        if r.ok:
            return r.json().get("result", [])
    except Exception as e:
        print(f"[WARN] getUpdates: {e}")
    return []

# ─── Bot 模式 ───
def do_full_pipeline():
    """执行完整数据流水线, 返回(news_results, hot7, picks, sector_list, quotes, streaks)"""
    global API_CALL_COUNT, START_TIME
    API_CALL_COUNT = 0
    START_TIME = time.time()

    # 1. 抓新闻
    news_list = fetch_ths_news()
    if not news_list:
        print("[WARN] 未获取到新闻")
        return [], [], [], [], {}, {}

    # 2. 情绪分析
    news_results, hot7, sector_list = analyze_sentiment(news_list)

    # 3. 收集所有相关股票代码
    all_codes = set()
    for h in hot7:
        all_codes.add(stock_prefix(h["code"]))
    for item in news_results[:50]:
        for s in item.get("stocks", []):
            all_codes.add(stock_prefix(s["code"]))
    # ETF代码
    for sec in sector_list:
        if sec["etf"]:
            all_codes.add(stock_prefix(sec["etf"]))

    # 4. 获取行情
    codes_list = list(all_codes)
    quotes = fetch_tencent_quotes(codes_list)

    # 5. K线 + 连涨 (仅Hot7)
    streaks = {}
    for h in hot7:
        sym = stock_prefix(h["code"])
        klines = fetch_kline(sym, 20)
        streaks[sym] = calc_streaks(klines)
        time.sleep(0.15)

    # 6. 实时精选
    picks = build_realtime_picks(news_results, hot7, quotes, streaks)

    elapsed = time.time() - START_TIME
    print(f"[INFO] 流水线完成: {elapsed:.1f}s, API调用 {API_CALL_COUNT} 次")

    return news_results, hot7, picks, sector_list, quotes, streaks

def do_full_summary():
    """执行完整流水线 + 生成HTML + 推送"""
    news_results, hot7, picks, sector_list, quotes, streaks = do_full_pipeline()
    if not hot7 and not picks:
        send_telegram("⚠️ 暂无利好数据")
        return

    # 生成HTML
    html = generate_html(news_results, hot7, picks, sector_list, quotes, streaks)
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[INFO] 面板已生成: {output_path}")

    # 推送摘要
    msg = format_summary_msg(hot7, picks, sector_list)
    send_telegram(msg)
    print("[INFO] 推送完成")

def bot_mode():
    """Bot模式: 命令监听 + 定时推送 + 利好预警"""
    print("[BOT] 启动Bot模式...")

    # 消费旧消息
    old_updates = get_updates(offset=None, timeout=1)
    offset = None
    if old_updates:
        offset = old_updates[-1]["update_id"] + 1
        print(f"[BOT] 跳过 {len(old_updates)} 条旧消息")

    last_push_time = 0
    last_alert_ids = set()
    push_interval = 15 * 60  # 15分钟

    send_telegram("🤖 A股利好监控Bot已启动\n发送 <b>发送</b> 获取即时汇总\n盘中(9:15-15:00)每15分钟自动推送")

    while True:
        try:
            # 检查命令
            updates = get_updates(offset=offset, timeout=10)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = msg.get("chat", {}).get("id")
                if text == "发送" or text == "/summary":
                    print(f"[BOT] 收到命令: {text} from {chat_id}")
                    send_telegram("⏳ 正在分析，请稍候...", chat_id=chat_id)
                    do_full_summary()

            # 盘中定时推送
            now = datetime.now()
            hour_min = now.hour * 100 + now.minute
            is_trading = (915 <= hour_min <= 1500) and now.weekday() < 5
            if is_trading and (time.time() - last_push_time) >= push_interval:
                print(f"[BOT] 定时推送 {now.strftime('%H:%M')}")
                do_full_summary()
                last_push_time = time.time()

            # 利好预警: 检查新的高分新闻
            if is_trading and (time.time() - last_push_time) > 60:
                try:
                    news_list = fetch_ths_news()
                    if news_list:
                        results, _, _ = analyze_sentiment(news_list)
                        for item in results[:5]:
                            nid = str(item["id"])
                            if item["score"] >= 4 and nid not in last_alert_ids:
                                last_alert_ids.add(nid)
                                alert_msg = (
                                    f"🚨 <b>利好预警 [{item['level']}]</b>\n"
                                    f"<b>{item['title']}</b>\n"
                                    f"评分: {item['score']} | {fmt_date(item['ctime'])}"
                                )
                                if item["stocks"]:
                                    names = ", ".join(s["name"] for s in item["stocks"][:5])
                                    alert_msg += f"\n相关: {names}"
                                send_telegram(alert_msg)
                except Exception as e:
                    print(f"[WARN] 利好预警检查失败: {e}")

            time.sleep(2)

        except KeyboardInterrupt:
            print("\n[BOT] 退出")
            break
        except Exception as e:
            print(f"[BOT] 异常: {e}")
            traceback.print_exc()
            time.sleep(10)

# ─── 主函数 ───
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--run"

    if mode == "--bot":
        bot_mode()
    elif mode == "--push":
        do_full_summary()
    else:
        # 默认: 生成面板
        news_results, hot7, picks, sector_list, quotes, streaks = do_full_pipeline()
        html = generate_html(news_results, hot7, picks, sector_list, quotes, streaks)
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[INFO] 面板已生成: {output_path}")
        print(f"[INFO] 耗时 {time.time()-START_TIME:.1f}s, API调用 {API_CALL_COUNT} 次")

if __name__ == "__main__":
    main()
