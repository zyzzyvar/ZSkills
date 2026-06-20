#!/usr/bin/env python3
"""
A股 Tracker - 投资组合管理(零依赖)

管理持仓股(含数量、成本价)与关注股,持久化存储,跨会话有效。
存储: $ASTOCK_DIR/portfolio.json (默认 ~/.astock-tracker/)

用法:
  python3 portfolio.py hold-add --code 600519 --name 贵州茅台 --shares 100 --cost 1680.5
  python3 portfolio.py hold-add --code 000858 --name 五粮液 --shares 500 --cost 142.3
  python3 portfolio.py watch-add --code 300750 --name 宁德时代 --note "等回调到180"
  python3 portfolio.py list
  python3 portfolio.py hold-update --code 600519 --shares 200 --cost 1655.0  # 加仓后更新
  python3 portfolio.py hold-remove --code 600519        # 清仓
  python3 portfolio.py watch-remove --code 300750
  python3 portfolio.py codes                            # 仅输出所有代码(供批量分析)
"""
import argparse, json, os, sys
from datetime import datetime

DATA_DIR = os.environ.get("ASTOCK_DIR", os.path.join(os.path.expanduser("~"), ".astock-tracker"))
DATA_FILE = os.path.join(DATA_DIR, "portfolio.json")


def _load():
    if not os.path.exists(DATA_FILE):
        return {"holdings": [], "watchlist": []}
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save(db):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def _out(o): print(json.dumps(o, ensure_ascii=False, indent=2))
def _err(m, **e): _out({"ok": False, "error": m, **e}); sys.exit(1)
def _now(): return datetime.now().strftime("%Y-%m-%d %H:%M")


def _norm_code(code):
    code = code.strip()
    if not code.isdigit() or len(code) != 6:
        _err(f"股票代码应为6位数字: {code!r}")
    return code


def _market(code):
    """根据代码判断交易所(供资金流接口用)。"""
    if code.startswith(("60", "68", "11", "51")): return "sh"
    if code.startswith(("00", "30", "12", "15")): return "sz"
    if code.startswith(("8", "4", "92")): return "bj"
    return "sh"


def cmd_hold_add(args):
    db = _load()
    code = _norm_code(args.code)
    for h in db["holdings"]:
        if h["code"] == code:
            _err(f"{code} 已在持仓中,请用 hold-update 修改", existing=h)
    h = {"code": code, "name": args.name, "shares": args.shares,
         "cost": args.cost, "market": _market(code),
         "added_at": _now(), "note": args.note or ""}
    db["holdings"].append(h)
    db["watchlist"] = [w for w in db["watchlist"] if w["code"] != code]
    _save(db)
    _out({"ok": True, "holding": h})


def cmd_hold_update(args):
    db = _load()
    code = _norm_code(args.code)
    for h in db["holdings"]:
        if h["code"] == code:
            if args.shares is not None: h["shares"] = args.shares
            if args.cost is not None: h["cost"] = args.cost
            if args.name: h["name"] = args.name
            if args.note is not None: h["note"] = args.note
            h["updated_at"] = _now()
            _save(db); _out({"ok": True, "holding": h}); return
    _err(f"{code} 不在持仓中")


def cmd_hold_remove(args):
    db = _load(); code = _norm_code(args.code)
    n = len(db["holdings"])
    db["holdings"] = [h for h in db["holdings"] if h["code"] != code]
    if len(db["holdings"]) == n: _err(f"{code} 不在持仓中")
    _save(db); _out({"ok": True, "removed": code})


def cmd_watch_add(args):
    db = _load(); code = _norm_code(args.code)
    if any(w["code"] == code for w in db["watchlist"]):
        _err(f"{code} 已在关注列表")
    if any(h["code"] == code for h in db["holdings"]):
        _err(f"{code} 已在持仓中(持仓股默认就会被跟踪)")
    w = {"code": code, "name": args.name, "market": _market(code),
         "added_at": _now(), "note": args.note or ""}
    db["watchlist"].append(w)
    _save(db); _out({"ok": True, "watch": w})


def cmd_watch_remove(args):
    db = _load(); code = _norm_code(args.code)
    n = len(db["watchlist"])
    db["watchlist"] = [w for w in db["watchlist"] if w["code"] != code]
    if len(db["watchlist"]) == n: _err(f"{code} 不在关注列表")
    _save(db); _out({"ok": True, "removed": code})


def cmd_list(args):
    db = _load()
    _out({"ok": True,
          "holdings": db["holdings"], "watchlist": db["watchlist"],
          "holding_count": len(db["holdings"]),
          "watch_count": len(db["watchlist"])})


def cmd_codes(args):
    db = _load()
    holds = [{"code": h["code"], "name": h["name"], "market": h["market"]} for h in db["holdings"]]
    watch = [{"code": w["code"], "name": w["name"], "market": w["market"]} for w in db["watchlist"]]
    _out({"ok": True, "holdings": holds, "watchlist": watch})


def main():
    p = argparse.ArgumentParser(description="A股投资组合管理")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("hold-add"); a.add_argument("--code", required=True)
    a.add_argument("--name", required=True); a.add_argument("--shares", type=int, required=True)
    a.add_argument("--cost", type=float, required=True); a.add_argument("--note")
    a.set_defaults(func=cmd_hold_add)

    u = sub.add_parser("hold-update"); u.add_argument("--code", required=True)
    u.add_argument("--name"); u.add_argument("--shares", type=int)
    u.add_argument("--cost", type=float); u.add_argument("--note")
    u.set_defaults(func=cmd_hold_update)

    r = sub.add_parser("hold-remove"); r.add_argument("--code", required=True)
    r.set_defaults(func=cmd_hold_remove)

    wa = sub.add_parser("watch-add"); wa.add_argument("--code", required=True)
    wa.add_argument("--name", required=True); wa.add_argument("--note")
    wa.set_defaults(func=cmd_watch_add)

    wr = sub.add_parser("watch-remove"); wr.add_argument("--code", required=True)
    wr.set_defaults(func=cmd_watch_remove)

    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("codes").set_defaults(func=cmd_codes)

    args = p.parse_args(); args.func(args)


if __name__ == "__main__":
    main()
