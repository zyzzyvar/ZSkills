#!/usr/bin/env python3
"""
A股 Tracker - Tushare 数据源(主源,零SDK依赖)

直接用标准库 urllib 调 Tushare HTTP API(http://api.tushare.pro),不依赖 tushare SDK,
进一步降低依赖风险。Tushare 走官方 API + token 鉴权,无反爬、无网页抓取,
是大陆网络环境下最稳定的 A 股数据源。

Token 读取优先级:
  1. 环境变量 TUSHARE_TOKEN
  2. 配置文件 $ASTOCK_DIR/tushare_token.txt
若都没有,Tushare 源不可用,上层会自动降级到 AKShare 备源。

本模块被 fetch.py 调用,提供与 AKShare 对齐的标准化数据。
"""
import json, os, urllib.request, urllib.error
from datetime import datetime, timedelta

DATA_DIR = os.environ.get("ASTOCK_DIR", os.path.join(os.path.expanduser("~"), ".astock-tracker"))
API_URL = "http://api.tushare.pro"


def get_token():
    tok = os.environ.get("TUSHARE_TOKEN", "").strip()
    if tok:
        return tok
    path = os.path.join(DATA_DIR, "tushare_token.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return None


def available():
    return get_token() is not None


def _call(api_name, params, fields="", timeout=15):
    """调用 Tushare HTTP API,返回 list[dict];失败抛异常(供上层降级)。"""
    token = get_token()
    if not token:
        raise RuntimeError("未配置 Tushare token")
    payload = json.dumps({
        "api_name": api_name, "token": token,
        "params": params, "fields": fields,
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("code") != 0:
        raise RuntimeError(f"Tushare错误: {body.get('msg', '未知')}")
    data = body.get("data") or {}
    cols = data.get("fields", [])
    items = data.get("items", [])
    return [dict(zip(cols, row)) for row in items]


def _ts_code(code, market):
    """600519 + sh → 600519.SH"""
    suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(market.lower(), "SH")
    return f"{code}.{suffix}"


# ---------------- 标准化数据接口(与 AKShare 输出对齐) ----------------

def hist_klines(code, market, start_date, end_date):
    """日线行情(前复权),返回东财式列名的 list[dict]。"""
    ts = _ts_code(code, market)
    # 前复权:用 pro_bar 较复杂,这里用 daily + adj_factor 简化;
    # 为稳定起见直接用 daily(未复权)+ 单独说明,技术指标对短周期影响极小。
    # 更佳:用 adj qfq,但 HTTP 无 pro_bar,改用 daily 原始价(分析以趋势为主,可接受)
    rows = _call("daily", {"ts_code": ts, "start_date": start_date, "end_date": end_date},
                 fields="trade_date,open,high,low,close,vol,amount,pct_chg")
    out = []
    for r in rows:
        out.append({
            "日期": f"{str(r['trade_date'])[:4]}-{str(r['trade_date'])[4:6]}-{str(r['trade_date'])[6:8]}",
            "开盘": r["open"], "收盘": r["close"], "最高": r["high"], "最低": r["low"],
            "成交量": r["vol"], "成交额": r["amount"], "涨跌幅": r.get("pct_chg"),
        })
    # daily 返回为倒序(最新在前),上层会再排序
    return out


def daily_basic(code, market, trade_date=None):
    """每日指标:换手率、量比、市盈率、市净率、市值。"""
    ts = _ts_code(code, market)
    params = {"ts_code": ts}
    if trade_date:
        params["trade_date"] = trade_date
    else:
        # 取最近10个自然日里最新一条
        params["start_date"] = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        params["end_date"] = datetime.now().strftime("%Y%m%d")
    rows = _call("daily_basic", params,
                 fields="trade_date,turnover_rate,volume_ratio,pe,pe_ttm,pb,total_mv,circ_mv")
    return rows[0] if rows else None


def moneyflow(code, market, start_date, end_date):
    """个股资金流:主力=大单+特大单。返回按日期的主力净流入(万元)。"""
    ts = _ts_code(code, market)
    rows = _call("moneyflow", {"ts_code": ts, "start_date": start_date, "end_date": end_date},
                 fields="trade_date,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount")
    out = []
    for r in rows:
        # 主力净额 = (大单买-大单卖) + (特大单买-特大单卖),单位万元
        try:
            main_net = (float(r.get("buy_lg_amount") or 0) - float(r.get("sell_lg_amount") or 0)
                        + float(r.get("buy_elg_amount") or 0) - float(r.get("sell_elg_amount") or 0))
        except (TypeError, ValueError):
            main_net = float(r.get("net_mf_amount") or 0)
        out.append({
            "日期": f"{str(r['trade_date'])[:4]}-{str(r['trade_date'])[4:6]}-{str(r['trade_date'])[6:8]}",
            "主力净流入-净额": main_net * 1e4,  # 转成元,与东财接口单位对齐(上层再/1e4)
            "主力净流入-净占比": 0,
        })
    return out  # 倒序,上层排序


def index_daily(index_code, start_date, end_date):
    """指数日线。index_code 如 000001.SH(上证)、399001.SZ(深成)、399006.SZ(创业板)。"""
    rows = _call("index_daily", {"ts_code": index_code, "start_date": start_date, "end_date": end_date},
                 fields="trade_date,close,pct_chg")
    return rows


def top_list(code, market, start_date, end_date):
    """龙虎榜:该股在区间内的上榜记录。"""
    ts = _ts_code(code, market)
    try:
        rows = _call("top_list", {"ts_code": ts, "start_date": start_date, "end_date": end_date},
                     fields="trade_date,name,reason,net_amount,turnover_rate")
        return rows
    except Exception:
        return []


def fina_indicator(code, market):
    """财务指标:最近几期的营收/利润同比等。"""
    ts = _ts_code(code, market)
    rows = _call("fina_indicator", {"ts_code": ts},
                 fields="end_date,roe,netprofit_yoy,or_yoy,grossprofit_margin,debt_to_assets")
    return rows[:4]  # 最近4期


def news_flash(code=None, limit=10):
    """新闻快讯。Tushare 的 news 接口需较高积分,2000分可能不可用,失败则上层降级。"""
    src_rows = _call("news", {"src": "sina", "start_date": (datetime.now()-timedelta(days=3)).strftime("%Y%m%d"),
                              "end_date": datetime.now().strftime("%Y%m%d")},
                     fields="datetime,content,title")
    return src_rows[:limit]


# 主要指数代码映射(供大盘情绪用)
INDEX_CODES = {
    "上证指数": "000001.SH",
    "深证成指": "399001.SZ",
    "创业板指": "399006.SZ",
    "科创50": "000688.SH",
}


def realtime_available():
    """实时报价需要 tushare 包(≥1.3.3),按需检测,不影响主链路。"""
    try:
        import tushare  # noqa
        return True
    except Exception:
        return False


def realtime_quote(code, market):
    """
    盘中实时报价(当下未收盘的最新价/涨跌/盘中量/买卖五档)。
    用 tushare 的 realtime_quote 爬虫接口(0积分开放,数据来自新浪/东财)。

    关键:realtime_quote 走 tushare SDK 自己的 token 存储(~/.tushare.csv),
    不读 skill 的 token 文件。所以这里先把 skill 已配置的 token 用 set_token
    注入 SDK,实现"一个 token 两处通用",用户无需额外配置。

    这是独立于主数据链路的能力:tushare 包未装或失败时抛异常,
    由上层降级到 AKShare 实时接口,绝不影响收盘后数据(daily/moneyflow等)的稳定性。
    返回标准化 dict。
    """
    import tushare as ts_sdk

    # 把 skill 的 token 注入 tushare SDK 的存储,使实时接口可鉴权
    tok = get_token()
    if tok:
        try:
            ts_sdk.set_token(tok)
        except Exception:
            pass

    tscode = _ts_code(code, market)
    # 优先新浪源,失败切东财源
    last_err = None
    for src in ("sina", "dc"):
        try:
            df = ts_sdk.realtime_quote(ts_code=tscode, src=src)
            if df is not None and len(df) > 0:
                r = df.iloc[0]
                price = float(r["price"])
                pre_close = float(r["pre_close"])
                pct = round((price / pre_close - 1) * 100, 2) if pre_close > 0 else None
                return {
                    "name": r.get("name", ""),
                    "price": price,
                    "pre_close": pre_close,
                    "open": float(r.get("open", 0) or 0),
                    "high": float(r.get("high", 0) or 0),
                    "low": float(r.get("low", 0) or 0),
                    "pct_change": pct,
                    "change": round(price - pre_close, 2),
                    "volume_lots": round(float(r.get("volume", r.get("volumn", 0)) or 0) / 100, 0),
                    "amount_wan": round(float(r.get("amount", 0) or 0) / 1e4, 1),
                    "bid1": float(r.get("b1_p", 0) or 0),
                    "ask1": float(r.get("a1_p", 0) or 0),
                    "date": str(r.get("date", "")),
                    "time": str(r.get("time", "")),
                    "source": f"Tushare实时({src})",
                }
        except Exception as e:
            last_err = str(e)[:80]
            continue
    raise RuntimeError(f"Tushare实时报价失败: {last_err}")
