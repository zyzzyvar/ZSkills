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

    # ---- 2. 个股资金流:东财(完整历史) → 同花顺(当日即时,降级兜底) ----
    # 东财走 push2his(反爬较严);同花顺走 10jqka,数据源独立,作为备源绕开东财反爬。
    def _fund_em():
        return ak.stock_individual_fund_flow(stock=code, market=market)

    def _fund_ths():
        # 同花顺即时全市场资金流,按代码筛选本股 → 统一成东财式单行格式
        import pandas as pd
        df = ak.stock_fund_flow_individual(symbol="即时")
        df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)
        row = df[df["股票代码"] == code]
        if len(row) == 0:
            raise Exception("同花顺即时资金流中未找到该股(可能停牌或非当日交易)")
        r = row.iloc[0]
        # 同花顺"净额"单位为元/万元视版本而定,统一尝试解析为万元
        net = r.get("净额", r.get("资金流入净额", 0))
        try:
            net_val = float(str(net).replace("亿", "e8").replace("万", "e4").replace("元", ""))
        except Exception:
            net_val = float(pd.to_numeric(net, errors="coerce") or 0)
        # 包装成与东财历史相同的结构(仅当日一行)
        return pd.DataFrame([{"日期": datetime.now().strftime("%Y-%m-%d"),
                              "主力净流入-净额": net_val,
                              "主力净流入-净占比": float(pd.to_numeric(r.get("净占比", 0), errors="coerce") or 0)}])

    fund_r = robust_fetch(f"fund:{code}", [("东财", _fund_em, True), ("同花顺", _fund_ths, False)])
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
                             "main_pct": float(r.get("主力净流入-净占比", 0))})
            except Exception:
                continue
        if rows:
            m5 = round(sum(x["main_net_wan"] for x in rows[-5:]), 1)
            m10 = round(sum(x["main_net_wan"] for x in rows[-10:]), 1)
            partial = len(rows) < 5  # 同花顺备源只有当日一行
            result["data"]["fund_flow"] = {
                "main_net_today_wan": rows[-1]["main_net_wan"],
                "main_net_5d_wan": None if partial else m5,
                "main_net_10d_wan": None if partial else m10,
                "trend": ("仅当日数据(备源)" if partial else
                          ("持续流入" if m5 > 0 and m10 > 0 else
                           ("持续流出" if m5 < 0 and m10 < 0 else "进出交织"))),
                "recent": rows[-7:],
                "note": "备源仅提供当日资金流,多日趋势暂缺" if partial else ""}
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
    def _mf_ths():
        # 同花顺大盘资金流(独立源),取最新一行的主力净额
        import pandas as pd
        df = ak.stock_fund_flow_big_deal()
        return df
    mf_r = robust_fetch("market_flow", [("东财", _mf, True), ("同花顺", _mf_ths, False)], cache_hours=12)
    result["data_sources"]["market_flow"] = mf_r["source"]
    if mf_r["ok"] and mf_r["data"]:
        try:
            last = mf_r["data"][-1]
            if "主力净流入-净额" in last:
                result["data"]["market_main_net_yi"] = round(float(last["主力净流入-净额"]) / 1e8, 1)
            else:
                # 同花顺备源结构不同,保留原始供分析参考
                result["data"]["market_flow_raw"] = last
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
    # 按"维度"组织,每个维度列出 [主源, 备源...],只要有一个通过该维度即可用
    dimensions = {
        "历史K线": [
            ("东财", lambda: ak.stock_zh_a_hist(symbol="000001", period="daily",
                start_date="20260601", end_date="20260611", adjust="qfq"), True),
            ("腾讯", lambda: ak.stock_zh_a_hist_tx(symbol="sz000001",
                start_date="20260601", end_date="20260611"), False),
        ],
        "个股资金流": [
            ("东财", lambda: ak.stock_individual_fund_flow(stock="000001", market="sz"), True),
            ("同花顺", lambda: ak.stock_fund_flow_individual(symbol="即时"), False),
        ],
        "大盘资金流": [
            ("东财", lambda: ak.stock_market_fund_flow(), True),
            ("同花顺", lambda: ak.stock_fund_flow_big_deal(), False),
        ],
        "指数行情": [
            ("东财", lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"), True),
            ("新浪", lambda: ak.stock_zh_index_spot_sina(), False),
        ],
        "千股千评": [
            ("东财", lambda: ak.stock_comment_detail_zhpj_lspf_em(symbol="000001"), True),
        ],
        "个股新闻": [
            ("东财", lambda: ak.stock_news_em(symbol="000001"), True),
        ],
    }
    dim_results = {}
    for dim, sources in dimensions.items():
        dim_ok = False
        used = None
        tried = []
        for name, fn, is_em in sources:
            try:
                if is_em:
                    _throttle_em()
                df = fn()
                if df is not None and len(df) > 0:
                    dim_ok = True
                    used = name
                    break
                tried.append(f"{name}:空")
            except Exception as e:
                tried.append(f"{name}:{str(e)[:40]}")
        dim_results[dim] = {"available": dim_ok, "source": used, "tried": tried}

    ok_dims = sum(1 for v in dim_results.values() if v["available"])
    total = len(dimensions)
    return {
        "as_of": _now(),
        "akshare_version": ak.__version__,
        "dimensions_available": f"{ok_dims}/{total}",
        "all_critical_ok": all(dim_results[d]["available"] for d in ["历史K线", "个股资金流", "指数行情"]),
        "verdict": ("✅ 可用:核心维度均有可用数据源" if ok_dims >= 5 and
                    dim_results["历史K线"]["available"] and dim_results["个股资金流"]["available"]
                    else "⚠ 部分维度全部源失败,请查看 details 并考虑升级 akshare"),
        "details": dim_results,
        "note": "判定标准是'每个维度至少一个源可用',而非接口总数。"
                "某维度主源(东财)失败但备源(同花顺/腾讯/新浪)通过,即视为该维度可用。",
    }


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
