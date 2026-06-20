#!/usr/bin/env python3
"""
A股 Tracker - 每日批量数据采集驱动

为 cron 定时任务提供"一键采集全部持仓+关注股数据"的能力。
它只负责把所有股票的 snapshot + 大盘 market 数据汇总成一个 JSON 落盘,
真正的"专家分析"由 Hermes(LLM)读取这份 JSON 后完成。

设计意图:把"取数"(慢、需联网、可定时)和"分析"(LLM)解耦。
cron 在固定时间跑本脚本取数 → 触发 Hermes 读取最新 JSON 做分析并推送。

用法:
  python3 daily_brief.py --session pre     # 盘前
  python3 daily_brief.py --session noon    # 午间
  python3 daily_brief.py --session post    # 盘后
  python3 daily_brief.py --session post --lookback 30

输出: $ASTOCK_DIR/briefs/<日期>_<session>.json
"""
import argparse, json, os, subprocess, sys
from datetime import datetime

DATA_DIR = os.environ.get("ASTOCK_DIR", os.path.join(os.path.expanduser("~"), ".astock-tracker"))
BRIEF_DIR = os.path.join(DATA_DIR, "briefs")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _run(args):
    """调用同目录脚本,返回解析后的 JSON。"""
    r = subprocess.run([sys.executable] + args, capture_output=True, text=True,
                       cwd=SCRIPT_DIR, timeout=120)
    if r.returncode != 0:
        return {"ok": False, "error": r.stderr[:200]}
    try:
        return json.loads(r.stdout)
    except Exception as e:
        return {"ok": False, "error": f"解析失败: {e}", "raw": r.stdout[:200]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True, choices=["pre", "noon", "post"])
    p.add_argument("--lookback", type=int, default=20)
    args = p.parse_args()

    portfolio = _run(["portfolio.py", "codes"])
    if not portfolio.get("ok"):
        print(json.dumps({"ok": False, "error": "读取持仓失败", "detail": portfolio}, ensure_ascii=False))
        sys.exit(1)

    stocks = portfolio["holdings"] + portfolio["watchlist"]
    brief = {
        "session": args.session,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "market": _run(["fetch.py", "market"]),
        "holdings": [], "watchlist": [],
    }

    full = _run(["portfolio.py", "list"])
    hold_map = {h["code"]: h for h in full.get("holdings", [])}

    for s in stocks:
        snap = _run(["fetch.py", "snapshot", "--code", s["code"],
                     "--market", s["market"], "--lookback", str(args.lookback)])
        item = {"code": s["code"], "name": s["name"], "snapshot": snap}
        if s["code"] in hold_map:
            h = hold_map[s["code"]]
            item["position"] = {"shares": h["shares"], "cost": h["cost"], "note": h.get("note", "")}
            brief["holdings"].append(item)
        else:
            item["note"] = s.get("note", "")
            brief["watchlist"].append(item)
        # 批量采集多只股票时,股票之间留间隔,降低东财封IP风险
        import time as _t, random as _r
        _t.sleep(_r.uniform(1.0, 2.0))

    os.makedirs(BRIEF_DIR, exist_ok=True)
    path = os.path.join(BRIEF_DIR, f"{brief['date']}_{args.session}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(brief, f, ensure_ascii=False, indent=2, default=str)

    # 输出摘要(供 cron 日志和 Hermes 感知)
    print(json.dumps({
        "ok": True, "brief_file": path, "session": args.session,
        "holdings_count": len(brief["holdings"]),
        "watchlist_count": len(brief["watchlist"]),
        "hint": "Hermes 现在应读取 brief_file,按 SKILL.md 的对应时段框架做专家分析并推送给用户",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
