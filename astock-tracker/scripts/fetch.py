#!/usr/bin/env python3
"""
A股 Tracker - 数据采集引擎

封装 AKShare 接口,为单只股票汇总一份"分析所需的全部事实",输出结构化 JSON
供上层 LLM 做专家分析。本脚本只负责取数和计算客观指标,不做主观判断。

依赖: akshare (pip install akshare --break-system-packages)
存储缓存: $ASTOCK_DIR/cache/

用法:
  python3 fetch.py snapshot --code 600519 --market sh        # 单股全维度快照
  python3 fetch.py snapshot --code 600519 --market sh --lookback 30
  python3 fetch.py market                                     # 大盘与北向情绪
  python3 fetch.py news --code 600519                         # 个股新闻
  python3 fetch.py selfcheck                                  # 接口连通性自检

设计原则:
  - 每个数据维度独立 try/except,单点失败不影响整体,失败项标记 unavailable
  - 关键技术指标(均线/量比/MACD/资金流趋势)在本地用 pandas 计算,不依赖远端
  - 所有金额统一转为"万元"便于阅读
"""
import argparse, json, os, sys, time
from datetime import datetime, timedelta

DATA_DIR = os.environ.get("ASTOCK_DIR", os.path.join(os.path.expanduser("~"), ".astock-tracker"))
CACHE_DIR = os.path.join(DATA_DIR, "cache")


def _out(o): print(json.dumps(o, ensure_ascii=False, indent=2, default=str))
def _now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _retry(fn, tries=3, delay=1.5):
    """接口不稳定时重试;全部失败返回 (None, 错误信息)。"""
    last = None
    for i in range(tries):
        try:
            return fn(), None
        except Exception as e:
            last = str(e)[:80]
            time.sleep(delay * (i + 1))
    return None, last


def _safe(d, default="—"):
    return default if d is None else d


def snapshot(code, market, lookback):
    import akshare as ak
    import pandas as pd
    import numpy as np

    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=lookback * 2 + 80)).strftime("%Y%m%d")
    result = {"code": code, "market": market, "as_of": _now(), "data": {}, "unavailable": []}

    # ---- 1. 历史K线(前复权)→ 价格与技术指标 ----
    def _hist():
        return ak.stock_zh_a_hist(symbol=code, period="daily",
                                  start_date=start, end_date=today, adjust="qfq")
    hist, err = _retry(_hist)
    if hist is not None and len(hist):
        hist = hist.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
                                    "最高": "high", "最低": "low", "成交量": "vol",
                                    "成交额": "amount", "涨跌幅": "pct", "换手率": "turnover"})
        hist = hist.sort_values("date").reset_index(drop=True)
        close = hist["close"].astype(float)
        vol = hist["vol"].astype(float)

        def ma(n): return round(close.rolling(n).mean().iloc[-1], 2) if len(close) >= n else None
        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        macd = (dif - dea) * 2
        # 量比(今日量 / 过去5日均量)
        vol_ratio = round(vol.iloc[-1] / vol.iloc[-6:-1].mean(), 2) if len(vol) > 6 else None
        # 近 N 日表现
        recent = hist.tail(lookback)
        last = hist.iloc[-1]

        result["data"]["price"] = {
            "latest_close": round(float(last["close"]), 2),
            "pct_today": round(float(last["pct"]), 2),
            "turnover_today": round(float(last.get("turnover", 0)), 2),
            "ma5": ma(5), "ma10": ma(10), "ma20": ma(20), "ma60": ma(60),
            "vol_ratio": vol_ratio,
            "macd": {"dif": round(float(dif.iloc[-1]), 3),
                     "dea": round(float(dea.iloc[-1]), 3),
                     "macd": round(float(macd.iloc[-1]), 3),
                     "trend": "金叉趋势" if dif.iloc[-1] > dea.iloc[-1] else "死叉趋势"},
            "high_60d": round(float(close.tail(60).max()), 2),
            "low_60d": round(float(close.tail(60).min()), 2),
            f"pct_{lookback}d": round(float((last["close"] / hist.iloc[-min(lookback, len(hist))]["close"] - 1) * 100), 2),
        }
        result["data"]["recent_klines"] = [
            {"date": str(r["date"])[:10], "close": round(float(r["close"]), 2),
             "pct": round(float(r["pct"]), 2), "turnover": round(float(r.get("turnover", 0)), 2),
             "amount_yi": round(float(r["amount"]) / 1e8, 2)}
            for _, r in recent.iterrows()
        ]
    else:
        result["unavailable"].append({"price/klines": err})

    # ---- 2. 个股资金流(主力/超大单/大单) ----
    def _fund():
        return ak.stock_individual_fund_flow(stock=code, market=market)
    fund, err = _retry(_fund)
    if fund is not None and len(fund):
        recent_fund = fund.tail(lookback)
        rows = []
        for _, r in recent_fund.iterrows():
            rows.append({
                "date": str(r["日期"])[:10],
                "main_net_wan": round(float(r["主力净流入-净额"]) / 1e4, 1),
                "main_pct": float(r["主力净流入-净占比"]),
                "super_net_wan": round(float(r["超大单净流入-净额"]) / 1e4, 1),
            })
        main_5d = round(sum(x["main_net_wan"] for x in rows[-5:]), 1)
        main_10d = round(sum(x["main_net_wan"] for x in rows[-10:]), 1)
        result["data"]["fund_flow"] = {
            "main_net_5d_wan": main_5d, "main_net_10d_wan": main_10d,
            "trend": "持续流入" if main_5d > 0 and main_10d > 0 else
                     ("持续流出" if main_5d < 0 and main_10d < 0 else "进出交织"),
            "recent": rows[-7:],
        }
    else:
        result["unavailable"].append({"fund_flow": err})

    # ---- 3. 北向持股(沪深港通) ----
    def _hsgt():
        return ak.stock_hsgt_individual_em(stock=code)
    hsgt, err = _retry(_hsgt, tries=2)
    if hsgt is not None and len(hsgt):
        try:
            last = hsgt.iloc[-1]
            result["data"]["northbound"] = {k: str(last[k]) for k in hsgt.columns[:6]}
        except Exception:
            result["unavailable"].append({"northbound": "解析失败"})
    else:
        result["unavailable"].append({"northbound": err or "无北向数据(非标的或未开通)"})

    # ---- 4. 龙虎榜(近期是否上榜) ----
    def _lhb():
        end = datetime.now().strftime("%Y%m%d")
        st = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        return ak.stock_lhb_stock_detail_date_em(symbol=code)
    lhb, err = _retry(_lhb, tries=2)
    if lhb is not None and len(lhb):
        result["data"]["lhb_recent"] = {"on_list_times_30d": len(lhb),
                                        "latest": str(lhb.iloc[-1].to_dict())[:200]}
    else:
        result["data"]["lhb_recent"] = {"on_list_times_30d": 0, "note": "近30日未上龙虎榜"}

    # ---- 5. 千股千评(机构参与度/综合评分) ----
    def _comment():
        return ak.stock_comment_detail_zhpj_lspf_em(symbol=code)
    cmt, err = _retry(_comment, tries=2)
    if cmt is not None and len(cmt):
        last = cmt.iloc[-1]
        result["data"]["comment"] = {k: str(last[k]) for k in cmt.columns[:5]}
    else:
        result["unavailable"].append({"comment": err})

    # ---- 6. 财务摘要(基本面) ----
    def _fin():
        return ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
    fin, err = _retry(_fin, tries=2)
    if fin is not None and len(fin):
        # 取最近2期关键指标
        result["data"]["financials"] = fin.tail(2).to_dict("records")
    else:
        result["unavailable"].append({"financials": err})

    return result


def market_sentiment():
    import akshare as ak
    result = {"as_of": _now(), "data": {}, "unavailable": []}

    def _index():
        return ak.stock_zh_index_spot_em(symbol="沪深重要指数")
    idx, err = _retry(_index)
    if idx is not None and len(idx):
        keep = idx[idx["名称"].isin(["上证指数", "深证成指", "创业板指", "科创50"])]
        result["data"]["indices"] = [
            {"name": r["名称"], "close": float(r["最新价"]), "pct": float(r["涨跌幅"])}
            for _, r in keep.iterrows()]
    else:
        result["unavailable"].append({"indices": err})

    def _market_flow():
        return ak.stock_market_fund_flow()
    mf, err = _retry(_market_flow)
    if mf is not None and len(mf):
        last = mf.iloc[-1]
        result["data"]["market_main_net_yi"] = round(float(last["主力净流入-净额"]) / 1e8, 1)
    else:
        result["unavailable"].append({"market_flow": err})

    # 北向资金当日
    def _north():
        return ak.stock_hsgt_fund_flow_summary_em()
    nb, err = _retry(_north, tries=2)
    if nb is not None and len(nb):
        result["data"]["northbound_summary"] = nb.to_dict("records")[:4]
    else:
        result["unavailable"].append({"northbound": err})

    return result


def news(code):
    import akshare as ak
    def _n():
        return ak.stock_news_em(symbol=code)
    n, err = _retry(_n, tries=2)
    if n is not None and len(n):
        items = n.head(8)
        return {"code": code, "as_of": _now(),
                "news": [{"title": r.get("新闻标题", ""), "time": str(r.get("发布时间", "")),
                          "source": r.get("文章来源", "")} for _, r in items.iterrows()]}
    return {"code": code, "as_of": _now(), "news": [], "error": err}


def selfcheck():
    """逐个接口连通性自检,部署后先跑这个确认环境正常。"""
    import akshare as ak
    checks = {}
    probes = {
        "历史K线": lambda: ak.stock_zh_a_hist(symbol="000001", period="daily",
                          start_date="20260601", end_date="20260611", adjust="qfq"),
        "个股资金流": lambda: ak.stock_individual_fund_flow(stock="000001", market="sz"),
        "大盘资金流": lambda: ak.stock_market_fund_flow(),
        "指数行情": lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"),
        "千股千评": lambda: ak.stock_comment_detail_zhpj_lspf_em(symbol="000001"),
        "个股新闻": lambda: ak.stock_news_em(symbol="000001"),
    }
    for name, fn in probes.items():
        df, err = _retry(fn, tries=2, delay=1)
        checks[name] = {"ok": df is not None, "rows": (len(df) if df is not None else 0),
                        "error": err}
    ok_n = sum(1 for v in checks.values() if v["ok"])
    return {"as_of": _now(), "passed": f"{ok_n}/{len(probes)}", "checks": checks}


def main():
    p = argparse.ArgumentParser(description="A股数据采集引擎")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot")
    s.add_argument("--code", required=True)
    s.add_argument("--market", required=True, choices=["sh", "sz", "bj"])
    s.add_argument("--lookback", type=int, default=20)

    sub.add_parser("market")
    nw = sub.add_parser("news"); nw.add_argument("--code", required=True)
    sub.add_parser("selfcheck")

    args = p.parse_args()
    if args.cmd == "snapshot":
        _out(snapshot(args.code, args.market, args.lookback))
    elif args.cmd == "market":
        _out(market_sentiment())
    elif args.cmd == "news":
        _out(news(args.code))
    elif args.cmd == "selfcheck":
        _out(selfcheck())


if __name__ == "__main__":
    main()
