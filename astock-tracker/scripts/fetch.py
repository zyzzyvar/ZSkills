#!/usr/bin/env python3
"""
A股 Tracker - 数据采集引擎(大陆网络韧性版)

封装 AKShare 接口,为单只股票汇总分析所需的全部事实,输出结构化 JSON。
针对大陆网络环境做了加固(详见 datasource.py):
  - 关键维度多源降级(东财/新浪/腾讯/雪球),主源失败自动切备源
  - 东财接口限频,防止高频请求被封 IP
  - 缓存兜底,短时网络抖动时用最近缓存,标注时效,保证分析不中断

依赖: akshare (建议用国内镜像安装,见 README)
缓存: $ASTOCK_DIR/cache/

用法:
  python3 fetch.py snapshot --code 600519 --market sh --lookback 20
  python3 fetch.py market
  python3 fetch.py news --code 600519
  python3 fetch.py selfcheck
"""
import argparse, json, os, sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datasource import robust_fetch, code_with_market


def _out(o): print(json.dumps(o, ensure_ascii=False, indent=2, default=str))
def _now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_df(records):
    import pandas as pd
    return pd.DataFrame(records)


def snapshot(code, market, lookback):
    import akshare as ak
    import pandas as pd

    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=lookback * 2 + 90)).strftime("%Y%m%d")
    codes = code_with_market(code, market)
    result = {"code": code, "market": market, "as_of": _now(),
              "data": {}, "unavailable": [], "data_sources": {}, "stale_warnings": []}

    # ---- 1. 历史K线:东财 → 腾讯 → 新浪 三源降级 ----
    def _hist_em():
        return ak.stock_zh_a_hist(symbol=code, period="daily",
                                  start_date=start, end_date=today, adjust="qfq")
    def _hist_tx():
        df = ak.stock_zh_a_hist_tx(symbol=codes["tx"], start_date=start, end_date=today, adjust="qfq")
        return df.rename(columns={"date": "日期", "open": "开盘", "close": "收盘",
                                  "high": "最高", "low": "最低", "amount": "成交量"})
    def _hist_sina():
        df = ak.stock_zh_a_daily(symbol=codes["sina"], start_date=start, end_date=today, adjust="qfq")
        return df.rename(columns={"date": "日期", "open": "开盘", "close": "收盘",
                                  "high": "最高", "low": "最低", "volume": "成交量",
                                  "amount": "成交额"})

    hist_r = robust_fetch(f"hist:{code}:{lookback}", [
        ("东财", _hist_em, True), ("腾讯", _hist_tx, False), ("新浪", _hist_sina, False),
    ])
    result["data_sources"]["klines"] = hist_r["source"]
    if hist_r["from_cache"]:
        result["stale_warnings"].append(f"K线为缓存数据(约{hist_r.get('cache_age_hours')}小时前)")

    if hist_r["ok"]:
        hist = _to_df(hist_r["data"])
        colmap = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
                  "最低": "low", "成交量": "vol", "成交额": "amount",
                  "涨跌幅": "pct", "换手率": "turnover"}
        hist = hist.rename(columns={k: v for k, v in colmap.items() if k in hist.columns})
        if "date" in hist.columns and "close" in hist.columns:
            hist = hist.sort_values("date").reset_index(drop=True)
            close = pd.to_numeric(hist["close"], errors="coerce")
            vol = pd.to_numeric(hist.get("vol", pd.Series([0] * len(hist))), errors="coerce")
            if "pct" not in hist.columns:
                hist["pct"] = close.pct_change() * 100
            pct = pd.to_numeric(hist["pct"], errors="coerce")

            def ma(n): return round(float(close.rolling(n).mean().iloc[-1]), 2) if len(close) >= n else None
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9, adjust=False).mean()
            macd = (dif - dea) * 2
            vol_ratio = round(float(vol.iloc[-1] / vol.iloc[-6:-1].mean()), 2) if len(vol) > 6 and vol.iloc[-6:-1].mean() > 0 else None
            last = hist.iloc[-1]
            n = min(lookback, len(hist))

            result["data"]["price"] = {
                "latest_close": round(float(close.iloc[-1]), 2),
                "pct_today": round(float(pct.iloc[-1]), 2) if pd.notna(pct.iloc[-1]) else None,
                "turnover_today": round(float(pd.to_numeric(last.get("turnover", 0), errors="coerce") or 0), 2),
                "ma5": ma(5), "ma10": ma(10), "ma20": ma(20), "ma60": ma(60),
                "vol_ratio": vol_ratio,
                "macd": {"dif": round(float(dif.iloc[-1]), 3), "dea": round(float(dea.iloc[-1]), 3),
                         "macd": round(float(macd.iloc[-1]), 3),
                         "trend": "金叉趋势" if dif.iloc[-1] > dea.iloc[-1] else "死叉趋势"},
                "high_60d": round(float(close.tail(60).max()), 2),
                "low_60d": round(float(close.tail(60).min()), 2),
                f"pct_{lookback}d": round(float((close.iloc[-1] / close.iloc[-n] - 1) * 100), 2),
            }
            result["data"]["recent_klines"] = [
                {"date": str(r["date"])[:10], "close": round(float(r["close"]), 2),
                 "pct": round(float(pd.to_numeric(r.get("pct", 0), errors="coerce") or 0), 2)}
                for _, r in hist.tail(lookback).iterrows()
            ]
    else:
        result["unavailable"].append({"price/klines": hist_r["errors"]})

    # ---- 2. 个股资金流 ----
    def _fund():
        return ak.stock_individual_fund_flow(stock=code, market=market)
    fund_r = robust_fetch(f"fund:{code}", [("东财", _fund, True)])
    result["data_sources"]["fund_flow"] = fund_r["source"]
    if fund_r["from_cache"]:
        result["stale_warnings"].append(f"资金流为缓存数据(约{fund_r.get('cache_age_hours')}小时前)")
    if fund_r["ok"]:
        import pandas as pd
        fdf = _to_df(fund_r["data"]).tail(lookback)
        rows = []
        for _, r in fdf.iterrows():
            try:
                rows.append({"date": str(r["日期"])[:10],
                             "main_net_wan": round(float(r["主力净流入-净额"]) / 1e4, 1),
                             "main_pct": float(r["主力净流入-净占比"])})
            except Exception:
                continue
        if rows:
            m5 = round(sum(x["main_net_wan"] for x in rows[-5:]), 1)
            m10 = round(sum(x["main_net_wan"] for x in rows[-10:]), 1)
            result["data"]["fund_flow"] = {
                "main_net_5d_wan": m5, "main_net_10d_wan": m10,
                "trend": "持续流入" if m5 > 0 and m10 > 0 else ("持续流出" if m5 < 0 and m10 < 0 else "进出交织"),
                "recent": rows[-7:]}
    else:
        result["unavailable"].append({"fund_flow": fund_r["errors"]})

    # ---- 3. 龙虎榜 ----
    def _lhb():
        return ak.stock_lhb_stock_detail_date_em(symbol=code)
    lhb_r = robust_fetch(f"lhb:{code}", [("东财", _lhb, True)], cache_hours=168)
    if lhb_r["ok"] and lhb_r["data"]:
        result["data"]["lhb_recent"] = {"on_list_times": len(lhb_r["data"]),
                                        "note": f"近期上龙虎榜 {len(lhb_r['data'])} 次"}
    else:
        result["data"]["lhb_recent"] = {"on_list_times": 0, "note": "近期未上龙虎榜"}

    # ---- 4. 千股千评 ----
    def _cmt():
        return ak.stock_comment_detail_zhpj_lspf_em(symbol=code)
    cmt_r = robust_fetch(f"comment:{code}", [("东财", _cmt, True)], cache_hours=48)
    if cmt_r["ok"] and cmt_r["data"]:
        last = cmt_r["data"][-1]
        result["data"]["comment"] = {k: str(v) for k, v in list(last.items())[:5]}
    else:
        result["unavailable"].append({"comment": cmt_r["errors"]})

    # ---- 5. 财务摘要:同花顺 → 东财 ----
    def _fin_ths():
        return ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
    def _fin_em():
        return ak.stock_financial_abstract(symbol=code)
    fin_r = robust_fetch(f"fin:{code}", [("同花顺", _fin_ths, False), ("东财", _fin_em, True)], cache_hours=720)
    result["data_sources"]["financials"] = fin_r["source"]
    if fin_r["ok"] and fin_r["data"]:
        result["data"]["financials"] = fin_r["data"][-2:]
    else:
        result["unavailable"].append({"financials": fin_r["errors"]})

    return result


def market_sentiment():
    import akshare as ak
    result = {"as_of": _now(), "data": {}, "unavailable": [], "data_sources": {}, "stale_warnings": []}

    def _idx_em():
        return ak.stock_zh_index_spot_em(symbol="沪深重要指数")
    def _idx_sina():
        return ak.stock_zh_index_spot_sina()
    idx_r = robust_fetch("indices", [("东财", _idx_em, True), ("新浪", _idx_sina, False)], cache_hours=12)
    result["data_sources"]["indices"] = idx_r["source"]
    if idx_r["from_cache"]:
        result["stale_warnings"].append(f"指数为缓存数据(约{idx_r.get('cache_age_hours')}小时前)")
    if idx_r["ok"]:
        import pandas as pd
        df = _to_df(idx_r["data"])
        namecol = "名称" if "名称" in df.columns else df.columns[1]
        want = ["上证指数", "深证成指", "创业板指", "科创50"]
        rows = []
        for _, r in df.iterrows():
            if str(r.get(namecol, "")) in want:
                try:
                    rows.append({"name": str(r[namecol]),
                                 "close": float(r.get("最新价", r.get("最新", 0))),
                                 "pct": float(r.get("涨跌幅", 0))})
                except Exception:
                    continue
        result["data"]["indices"] = rows
    else:
        result["unavailable"].append({"indices": idx_r["errors"]})

    def _mf():
        return ak.stock_market_fund_flow()
    mf_r = robust_fetch("market_flow", [("东财", _mf, True)], cache_hours=12)
    if mf_r["ok"] and mf_r["data"]:
        try:
            last = mf_r["data"][-1]
            result["data"]["market_main_net_yi"] = round(float(last["主力净流入-净额"]) / 1e8, 1)
        except Exception:
            result["unavailable"].append({"market_flow": "解析失败"})
    else:
        result["unavailable"].append({"market_flow": mf_r["errors"]})

    return result


def news(code):
    import akshare as ak
    def _n():
        return ak.stock_news_em(symbol=code)
    r = robust_fetch(f"news:{code}", [("东财", _n, True)], cache_hours=24)
    if r["ok"] and r["data"]:
        items = r["data"][:8]
        return {"code": code, "as_of": _now(), "source": r["source"],
                "from_cache": r["from_cache"],
                "news": [{"title": it.get("新闻标题", ""), "time": str(it.get("发布时间", "")),
                          "source": it.get("文章来源", "")} for it in items]}
    return {"code": code, "as_of": _now(), "news": [], "errors": r["errors"]}


def selfcheck():
    import akshare as ak
    from datasource import _throttle_em
    checks = {}
    probes = {
        "历史K线-东财": (lambda: ak.stock_zh_a_hist(symbol="000001", period="daily",
                        start_date="20260601", end_date="20260611", adjust="qfq"), True),
        "历史K线-腾讯(备)": (lambda: ak.stock_zh_a_hist_tx(symbol="sz000001",
                        start_date="20260601", end_date="20260611"), False),
        "个股资金流": (lambda: ak.stock_individual_fund_flow(stock="000001", market="sz"), True),
        "大盘资金流": (lambda: ak.stock_market_fund_flow(), True),
        "指数-东财": (lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"), True),
        "指数-新浪(备)": (lambda: ak.stock_zh_index_spot_sina(), False),
        "千股千评": (lambda: ak.stock_comment_detail_zhpj_lspf_em(symbol="000001"), True),
        "个股新闻": (lambda: ak.stock_news_em(symbol="000001"), True),
    }
    for name, (fn, is_em) in probes.items():
        try:
            if is_em:
                _throttle_em()
            df = fn()
            checks[name] = {"ok": df is not None and len(df) > 0, "rows": len(df) if df is not None else 0}
        except Exception as e:
            checks[name] = {"ok": False, "error": str(e)[:80]}
    ok_n = sum(1 for v in checks.values() if v["ok"])
    return {"as_of": _now(), "passed": f"{ok_n}/{len(probes)}",
            "note": "K线/指数有备源,主源失败但备源通过即可正常工作",
            "akshare_version": ak.__version__, "checks": checks}


def main():
    p = argparse.ArgumentParser(description="A股数据采集引擎(大陆韧性版)")
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
