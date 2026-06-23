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
import tushare_source as ts


def _out(o): print(json.dumps(o, ensure_ascii=False, indent=2, default=str))
def _now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_df(records):
    import pandas as pd
    return pd.DataFrame(records)


def realtime(code, market):
    """
    盘中实时报价(独立能力,不影响收盘后数据链路)。
    Tushare realtime_quote(主) → AKShare 实时(备)。两者都失败才报错。
    """
    result = {"code": code, "market": market, "as_of": _now(), "data": None,
              "source": None, "errors": {}}
    # 主源:Tushare 实时(需 tushare 包,0积分可用)
    if ts.realtime_available():
        try:
            result["data"] = ts.realtime_quote(code, market)
            result["source"] = result["data"]["source"]
            return result
        except Exception as e:
            result["errors"]["tushare"] = str(e)[:80]
    else:
        result["errors"]["tushare"] = "未安装 tushare 包(pip install tushare),已尝试备源"

    # 备源:AKShare 实时快照
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        df["代码"] = df["代码"].astype(str).str.zfill(6)
        row = df[df["代码"] == code]
        if len(row) > 0:
            r = row.iloc[0]
            result["data"] = {
                "name": r.get("名称", ""),
                "price": float(r.get("最新价", 0) or 0),
                "pct_change": float(r.get("涨跌幅", 0) or 0),
                "change": float(r.get("涨跌额", 0) or 0),
                "high": float(r.get("最高", 0) or 0),
                "low": float(r.get("最低", 0) or 0),
                "open": float(r.get("今开", 0) or 0),
                "volume_lots": float(r.get("成交量", 0) or 0),
                "amount_wan": round(float(r.get("成交额", 0) or 0) / 1e4, 1),
                "turnover_rate": float(r.get("换手率", 0) or 0),
                "source": "AKShare实时(东财)",
            }
            result["source"] = result["data"]["source"]
            return result
        else:
            result["errors"]["akshare"] = "未找到该股(可能停牌)"
    except Exception as e:
        result["errors"]["akshare"] = str(e)[:80]

    return result


def snapshot(code, market, lookback, with_realtime=False):
    import akshare as ak
    import pandas as pd

    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=lookback * 2 + 90)).strftime("%Y%m%d")
    codes = code_with_market(code, market)
    result = {"code": code, "market": market, "as_of": _now(),
              "data": {}, "unavailable": [], "data_sources": {}, "stale_warnings": [],
              "primary_source": "Tushare" if ts.available() else "AKShare(未配置Tushare token)"}

    # ---- 0. 盘中实时报价(可选,独立能力,失败不影响其他维度) ----
    if with_realtime:
        rt = realtime(code, market)
        if rt["data"]:
            result["data"]["realtime"] = rt["data"]
            result["data_sources"]["realtime"] = rt["source"]
        else:
            result["unavailable"].append({"realtime": rt["errors"]})

    # ---- 1. 历史K线:Tushare(主) → 东财 → 腾讯 → 新浪 ----
    def _hist_ts():
        return _to_df(ts.hist_klines(code, market, start, today))
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

    hist_sources = []
    if ts.available():
        hist_sources.append(("Tushare", _hist_ts, False))
    hist_sources += [("东财", _hist_em, True), ("腾讯", _hist_tx, False), ("新浪", _hist_sina, False)]
    hist_r = robust_fetch(f"hist:{code}:{lookback}", hist_sources)
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

    # ---- 2. 个股资金流:Tushare moneyflow(主,无反爬) → 东财 → 同花顺 ----
    def _fund_ts():
        return _to_df(ts.moneyflow(code, market, start, today))
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
        net = r.get("净额", r.get("资金流入净额", 0))
        try:
            net_val = float(str(net).replace("亿", "e8").replace("万", "e4").replace("元", ""))
        except Exception:
            net_val = float(pd.to_numeric(net, errors="coerce") or 0)
        return pd.DataFrame([{"日期": datetime.now().strftime("%Y-%m-%d"),
                              "主力净流入-净额": net_val,
                              "主力净流入-净占比": float(pd.to_numeric(r.get("净占比", 0), errors="coerce") or 0)}])

    fund_sources = []
    if ts.available():
        fund_sources.append(("Tushare", _fund_ts, False))
    fund_sources += [("东财", _fund_em, True), ("同花顺", _fund_ths, False)]
    fund_r = robust_fetch(f"fund:{code}", fund_sources)
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

    # ---- 5. 财务:Tushare fina_indicator(主) → 同花顺 → 东财 ----
    def _fin_ts():
        rows = ts.fina_indicator(code, market)
        if not rows:
            raise Exception("Tushare财务无数据")
        return _to_df(rows)
    def _fin_ths():
        return ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
    def _fin_em():
        return ak.stock_financial_abstract(symbol=code)
    fin_sources = []
    if ts.available():
        fin_sources.append(("Tushare", _fin_ts, False))
    fin_sources += [("同花顺", _fin_ths, False), ("东财", _fin_em, True)]
    fin_r = robust_fetch(f"fin:{code}", fin_sources, cache_hours=720)
    result["data_sources"]["financials"] = fin_r["source"]
    if fin_r["ok"] and fin_r["data"]:
        result["data"]["financials"] = fin_r["data"][-4:] if fin_r["source"] == "Tushare" else fin_r["data"][-2:]
    else:
        result["unavailable"].append({"financials": fin_r["errors"]})

    # ---- 5b. 每日指标(Tushare 优势:量比/换手/PE/PB/市值) ----
    if ts.available():
        try:
            db = ts.daily_basic(code, market)
            if db:
                result["data"]["daily_basic"] = {
                    "turnover_rate": db.get("turnover_rate"),
                    "volume_ratio": db.get("volume_ratio"),
                    "pe_ttm": db.get("pe_ttm"), "pb": db.get("pb"),
                    "total_mv_yi": round(float(db["total_mv"]) / 1e4, 1) if db.get("total_mv") else None,
                    "circ_mv_yi": round(float(db["circ_mv"]) / 1e4, 1) if db.get("circ_mv") else None,
                }
                result["data_sources"]["daily_basic"] = "Tushare"
        except Exception as e:
            result["unavailable"].append({"daily_basic": str(e)[:60]})

    return result
    import akshare as ak
    result = {"as_of": _now(), "data": {}, "unavailable": [], "data_sources": {}, "stale_warnings": []}

    # 指数:Tushare index_daily(主) → 东财 → 新浪
    def _idx_ts():
        import pandas as pd
        rows = []
        end = datetime.now().strftime("%Y%m%d")
        st = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        for name, icode in ts.INDEX_CODES.items():
            try:
                d = ts.index_daily(icode, st, end)
                if d:
                    latest = d[0]  # 倒序,最新在前
                    rows.append({"名称": name, "最新价": latest["close"], "涨跌幅": latest.get("pct_chg", 0)})
            except Exception:
                continue
        if not rows:
            raise Exception("Tushare指数无数据")
        return pd.DataFrame(rows)
    def _idx_em():
        return ak.stock_zh_index_spot_em(symbol="沪深重要指数")
    def _idx_sina():
        return ak.stock_zh_index_spot_sina()
    idx_sources = []
    if ts.available():
        idx_sources.append(("Tushare", _idx_ts, False))
    idx_sources += [("东财", _idx_em, True), ("新浪", _idx_sina, False)]
    idx_r = robust_fetch("indices", idx_sources, cache_hours=12)
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
    today = datetime.now().strftime("%Y%m%d")
    st = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")

    # Tushare 状态(主源)
    tushare_status = {"configured": ts.available()}
    if ts.available():
        ts_checks = {}
        ts_probes = {
            "日线行情": lambda: ts.hist_klines("000001", "sz", st, today),
            "个股资金流": lambda: ts.moneyflow("000001", "sz", st, today),
            "每日指标": lambda: [ts.daily_basic("000001", "sz")],
            "指数行情": lambda: ts.index_daily("000001.SH", st, today),
        }
        for name, fn in ts_probes.items():
            try:
                d = fn()
                ts_checks[name] = {"ok": bool(d), "rows": len(d) if d else 0}
            except Exception as e:
                ts_checks[name] = {"ok": False, "error": str(e)[:80]}
        tushare_status["checks"] = ts_checks
        tushare_status["available_dims"] = sum(1 for v in ts_checks.values() if v["ok"])

    # AKShare 备源状态(按维度)
    dimensions = {
        "历史K线": [
            ("东财", lambda: ak.stock_zh_a_hist(symbol="000001", period="daily",
                start_date="20260601", end_date="20260611", adjust="qfq"), True),
            ("腾讯", lambda: ak.stock_zh_a_hist_tx(symbol="sz000001",
                start_date="20260601", end_date="20260611"), False),
        ],
        "千股千评": [("东财", lambda: ak.stock_comment_detail_zhpj_lspf_em(symbol="000001"), True)],
        "个股新闻": [("东财", lambda: ak.stock_news_em(symbol="000001"), True)],
    }
    ak_results = {}
    for dim, sources in dimensions.items():
        dim_ok, used = False, None
        for name, fn, is_em in sources:
            try:
                if is_em:
                    _throttle_em()
                df = fn()
                if df is not None and len(df) > 0:
                    dim_ok, used = True, name
                    break
            except Exception:
                continue
        ak_results[dim] = {"available": dim_ok, "source": used}

    # 综合判定:Tushare 配好且核心维度通 → 系统可用(AKShare 仅补充千股千评/新闻)
    ts_core_ok = ts.available() and tushare_status.get("available_dims", 0) >= 3
    ak_core_ok = ak_results["历史K线"]["available"]
    usable = ts_core_ok or ak_core_ok

    if ts_core_ok:
        verdict = "✅ 可用:Tushare 主源正常,数据稳定(无反爬风险)"
    elif ak_core_ok:
        verdict = "⚠ 可用但降级:Tushare 不可用,正使用 AKShare 备源(建议检查 token)"
    else:
        verdict = "❌ 不可用:Tushare 与 AKShare 核心维度均失败,请检查 token 与网络"

    return {
        "as_of": _now(),
        "akshare_version": ak.__version__,
        "tushare": tushare_status,
        "akshare_backup": ak_results,
        "usable": usable,
        "verdict": verdict,
        "note": "Tushare 是主源(官方API+token,无反爬,最稳)。只要 Tushare 核心维度可用即正常工作;"
                "AKShare 作为备源补充千股千评、个股新闻等 Tushare 未覆盖的维度。"
                "若 Tushare 未配置 token,会自动降级到 AKShare。",
    }


def main():
    p = argparse.ArgumentParser(description="A股数据采集引擎(大陆韧性版)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--code", required=True)
    s.add_argument("--market", required=True, choices=["sh", "sz", "bj"])
    s.add_argument("--lookback", type=int, default=20)
    s.add_argument("--realtime", action="store_true", help="附带盘中实时报价(盘中分析用)")
    sub.add_parser("market")
    rt = sub.add_parser("realtime", help="盘中实时报价(独立命令)")
    rt.add_argument("--code", required=True)
    rt.add_argument("--market", required=True, choices=["sh", "sz", "bj"])
    nw = sub.add_parser("news"); nw.add_argument("--code", required=True)
    sub.add_parser("selfcheck")
    args = p.parse_args()
    if args.cmd == "snapshot":
        _out(snapshot(args.code, args.market, args.lookback, with_realtime=args.realtime))
    elif args.cmd == "realtime":
        _out(realtime(args.code, args.market))
    elif args.cmd == "market":
        _out(market_sentiment())
    elif args.cmd == "news":
        _out(news(args.code))
    elif args.cmd == "selfcheck":
        _out(selfcheck())


if __name__ == "__main__":
    main()
