#!/usr/bin/env python3
"""
Hermes Secretary - 自建日历引擎(零依赖,Python 3.9+)

所有时间计算、冲突检测、空档搜索都由本脚本完成,绝不让 LLM 心算时间。
数据存储: $HERMES_CALENDAR_DIR/calendar.json (默认 ~/.hermes-secretary/)
所有输出均为 JSON,便于上层稳定解析。

用法示例:
  python hcal.py add --title "与张总会面" --start "2026-06-15 14:00" --duration 60 \
      --attendees "张总" --type external --priority high
  python hcal.py agenda --date 2026-06-15
  python hcal.py agenda --from 2026-06-15 --to 2026-06-19
  python hcal.py free --duration 60 --from 2026-06-15 --to 2026-06-17
  python hcal.py check --start "2026-06-15 15:00" --duration 30
  python hcal.py move --id <ID前缀> --start "2026-06-16 10:00"
  python hcal.py cancel --id <ID前缀> --reason "对方改期"
  python hcal.py show --id <ID前缀>
  python hcal.py load --date 2026-06-15        # 当日负荷分析
  python hcal.py config                         # 查看当前配置
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, date

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# ---------------------------------------------------------------- storage

DATA_DIR = os.environ.get(
    "HERMES_CALENDAR_DIR",
    os.path.join(os.path.expanduser("~"), ".hermes-secretary"),
)
DATA_FILE = os.path.join(DATA_DIR, "calendar.json")

DEFAULT_CONFIG = {
    "timezone": "America/Los_Angeles",
    "work_start": "09:00",          # 工作时间开始
    "work_end": "18:00",            # 工作时间结束
    "lunch_start": "12:00",         # 午餐保护时段
    "lunch_end": "13:00",
    "buffer_minutes": 15,           # 会议间默认缓冲
    "max_daily_meeting_hours": 6,   # 单日会议总时长上限(超出则告警)
    "work_days": [0, 1, 2, 3, 4],
    "airport_arrive_early_minutes": 90,
    "commute_to_airport_minutes": 60,   # 周一至周五 (Monday=0)
    "friday_internal_only_after": "13:00",  # 周五下午仅内部事务
}


def _load():
    if not os.path.exists(DATA_FILE):
        return {"config": dict(DEFAULT_CONFIG), "events": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        db = json.load(f)
    # 补齐新增配置项
    for k, v in DEFAULT_CONFIG.items():
        db.setdefault("config", {}).setdefault(k, v)
    return db


def _save(db):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)  # 原子写入,防止半写损坏


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _err(msg, **extra):
    _out({"ok": False, "error": msg, **extra})
    sys.exit(1)

# ---------------------------------------------------------------- time utils

def _parse_dt(s, cfg):
    """接受 'YYYY-MM-DD HH:MM' 或 ISO8601;统一为配置时区的 aware datetime。"""
    s = s.strip().replace("T", " ")
    fmts = ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"]
    dt = None
    for fmt in fmts:
        try:
            dt = datetime.strptime(s[:19], fmt)
            break
        except ValueError:
            continue
    if dt is None:
        _err(f"无法解析时间: {s!r},请使用 'YYYY-MM-DD HH:MM' 格式")
    if ZoneInfo:
        return dt.replace(tzinfo=ZoneInfo(cfg["timezone"]))
    return dt


def _parse_date(s):
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        _err(f"无法解析日期: {s!r},请使用 'YYYY-MM-DD' 格式")


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def _hm(daystr, hm, cfg):
    return _parse_dt(f"{daystr} {hm}", cfg)


def _now(cfg):
    if ZoneInfo:
        return datetime.now(ZoneInfo(cfg["timezone"]))
    return datetime.now()

# ---------------------------------------------------------------- events

def _active_events(db):
    return [e for e in db["events"] if e.get("status") == "confirmed"]


def _event_times(e, cfg):
    return _parse_dt(e["start"], cfg), _parse_dt(e["end"], cfg)


def _find_event(db, id_prefix):
    matches = [e for e in db["events"]
               if e["id"].startswith(id_prefix) and e.get("status") == "confirmed"]
    if not matches:
        _err(f"未找到 ID 以 {id_prefix!r} 开头的有效日程")
    if len(matches) > 1:
        _err("ID 前缀匹配到多个日程,请提供更长的前缀",
             candidates=[{"id": e["id"], "title": e["title"], "start": e["start"]} for e in matches])
    return matches[0]


def _conflicts(db, start, end, cfg, exclude_id=None):
    """返回 (硬冲突列表, 缓冲不足列表)。"""
    hard, soft = [], []
    buf = timedelta(minutes=cfg["buffer_minutes"])
    for e in _active_events(db):
        if e["id"] == exclude_id:
            continue
        es, ee = _event_times(e, cfg)
        brief = {"id": e["id"][:8], "title": e["title"],
                 "start": e["start"], "end": e["end"],
                 "priority": e.get("priority", "normal"), "type": e.get("type", "internal")}
        if start < ee and end > es:
            hard.append(brief)
        elif start < ee + buf and end + buf > es:
            soft.append(brief)
    return hard, soft


def _policy_warnings(start, end, cfg, db, exclude_id=None):
    """非阻断性提醒:工作时间外、午餐时段、周五下午外部会议、当日超载。"""
    warns = []
    d = start.date()
    daystr = d.isoformat()
    if start.weekday() not in cfg["work_days"]:
        warns.append("该时间在非工作日(周末)")
    ws, we = _hm(daystr, cfg["work_start"], cfg), _hm(daystr, cfg["work_end"], cfg)
    if start < ws or end > we:
        warns.append(f"超出工作时间 {cfg['work_start']}–{cfg['work_end']}")
    ls, le = _hm(daystr, cfg["lunch_start"], cfg), _hm(daystr, cfg["lunch_end"], cfg)
    if start < le and end > ls:
        warns.append(f"占用午餐保护时段 {cfg['lunch_start']}–{cfg['lunch_end']}")
    if start.weekday() == 4:
        cutoff = _hm(daystr, cfg["friday_internal_only_after"], cfg)
        if end > cutoff:
            warns.append("周五下午原则上仅安排内部事务,外部会议请确认")
    # 当日负荷
    total = end - start
    for e in _active_events(db):
        if e["id"] == exclude_id:
            continue
        es, ee = _event_times(e, cfg)
        if es.date() == d:
            total += ee - es
    hours = total.total_seconds() / 3600
    if hours > cfg["max_daily_meeting_hours"]:
        warns.append(f"加上此会议,当日会议总时长将达 {hours:.1f} 小时,"
                     f"超过 {cfg['max_daily_meeting_hours']} 小时上限")
    return warns

# ---------------------------------------------------------------- commands

def cmd_add(args):
    db = _load()
    cfg = db["config"]
    start = _parse_dt(args.start, cfg)
    if args.end:
        end = _parse_dt(args.end, cfg)
    else:
        end = start + timedelta(minutes=args.duration or 30)
    if end <= start:
        _err("结束时间必须晚于开始时间")

    hard, soft = _conflicts(db, start, end, cfg)
    warns = _policy_warnings(start, end, cfg, db)
    if hard and not args.force:
        _err("存在时间冲突,未创建。如确需双重预订请加 --force",
             conflicts=hard, buffer_issues=soft, policy_warnings=warns)

    ev = {
        "id": uuid.uuid4().hex,
        "title": args.title,
        "start": _fmt(start),
        "end": _fmt(end),
        "attendees": [a.strip() for a in (args.attendees or "").split(",") if a.strip()],
        "type": args.type,
        "priority": args.priority,
        "location": args.location or "",
        "notes": args.notes or "",
        "status": "confirmed",
        "created_at": _fmt(_now(cfg)),
        "history": [],
    }
    db["events"].append(ev)
    _save(db)
    _out({"ok": True, "event": ev,
          "forced_over_conflicts": hard if args.force else [],
          "buffer_issues": soft, "policy_warnings": warns})


def cmd_check(args):
    db = _load()
    cfg = db["config"]
    start = _parse_dt(args.start, cfg)
    end = _parse_dt(args.end, cfg) if args.end else start + timedelta(minutes=args.duration or 30)
    hard, soft = _conflicts(db, start, end, cfg, exclude_id=args.exclude_id)
    warns = _policy_warnings(start, end, cfg, db, exclude_id=args.exclude_id)
    _out({"ok": True, "slot": {"start": _fmt(start), "end": _fmt(end)},
          "available": not hard, "conflicts": hard,
          "buffer_issues": soft, "policy_warnings": warns})


def cmd_agenda(args):
    db = _load()
    cfg = db["config"]
    if args.date:
        d_from = d_to = _parse_date(args.date)
    else:
        d_from = _parse_date(getattr(args, "from"))
        d_to = _parse_date(args.to) if args.to else d_from
    events = []
    for e in _active_events(db):
        es, _ = _event_times(e, cfg)
        if d_from <= es.date() <= d_to:
            events.append(e)
    events.sort(key=lambda e: e["start"])
    _out({"ok": True, "from": d_from.isoformat(), "to": d_to.isoformat(),
          "timezone": cfg["timezone"], "count": len(events),
          "events": [{**e, "id": e["id"][:8]} for e in events]})


def cmd_free(args):
    db = _load()
    cfg = db["config"]
    dur = timedelta(minutes=args.duration)
    buf = timedelta(minutes=cfg["buffer_minutes"])
    d_from = _parse_date(getattr(args, "from"))
    d_to = _parse_date(args.to) if args.to else d_from
    now = _now(cfg)
    slots = []
    d = d_from
    while d <= d_to and len(slots) < args.max_slots:
        if d.weekday() in cfg["work_days"]:
            daystr = d.isoformat()
            ws = _hm(daystr, cfg["work_start"], cfg)
            we = _hm(daystr, cfg["work_end"], cfg)
            ls = _hm(daystr, cfg["lunch_start"], cfg)
            le = _hm(daystr, cfg["lunch_end"], cfg)
            # 候选窗口:上午段 + 下午段(自动避开午餐)
            windows = [(ws, ls), (le, we)]
            busy = []
            for e in _active_events(db):
                es, ee = _event_times(e, cfg)
                if es.date() == d:
                    busy.append((es - buf, ee + buf))  # 带缓冲的占用
            busy.sort()
            for w_start, w_end in windows:
                cursor = max(w_start, now + timedelta(minutes=30)) if d == now.date() else w_start
                for b_start, b_end in busy:
                    if b_start > cursor and cursor + dur <= min(b_start, w_end):
                        slots.append({"start": _fmt(cursor), "end": _fmt(cursor + dur)})
                        if len(slots) >= args.max_slots:
                            break
                    cursor = max(cursor, b_end)
                if len(slots) < args.max_slots and cursor + dur <= w_end:
                    slots.append({"start": _fmt(cursor), "end": _fmt(cursor + dur)})
                if len(slots) >= args.max_slots:
                    break
        d += timedelta(days=1)
    _out({"ok": True, "duration_minutes": args.duration,
          "search_range": [d_from.isoformat(), d_to.isoformat()],
          "rules_applied": ["工作时间内", "避开午餐时段", f"前后含 {cfg['buffer_minutes']} 分钟缓冲"],
          "slots": slots})


def cmd_move(args):
    db = _load()
    cfg = db["config"]
    ev = _find_event(db, args.id)
    old = {"start": ev["start"], "end": ev["end"]}
    duration = _parse_dt(ev["end"], cfg) - _parse_dt(ev["start"], cfg)
    start = _parse_dt(args.start, cfg)
    end = _parse_dt(args.end, cfg) if args.end else start + duration
    hard, soft = _conflicts(db, start, end, cfg, exclude_id=ev["id"])
    warns = _policy_warnings(start, end, cfg, db, exclude_id=ev["id"])
    if hard and not args.force:
        _err("新时间存在冲突,未改期。如确需双重预订请加 --force",
             conflicts=hard, buffer_issues=soft, policy_warnings=warns)
    ev["history"].append({"action": "moved", "from": old, "at": _fmt(_now(cfg)),
                          "reason": args.reason or ""})
    ev["start"], ev["end"] = _fmt(start), _fmt(end)
    _save(db)
    _out({"ok": True, "event": ev, "moved_from": old,
          "buffer_issues": soft, "policy_warnings": warns})


def cmd_cancel(args):
    db = _load()
    cfg = db["config"]
    ev = _find_event(db, args.id)
    ev["status"] = "cancelled"
    ev["history"].append({"action": "cancelled", "at": _fmt(_now(cfg)),
                          "reason": args.reason or ""})
    _save(db)
    _out({"ok": True, "cancelled": {"id": ev["id"][:8], "title": ev["title"],
                                    "start": ev["start"], "reason": args.reason or ""}})


def cmd_show(args):
    db = _load()
    ev = _find_event(db, args.id)
    _out({"ok": True, "event": ev})


def cmd_load_analysis(args):
    db = _load()
    cfg = db["config"]
    d = _parse_date(args.date)
    day_events = []
    total = timedelta()
    for e in _active_events(db):
        es, ee = _event_times(e, cfg)
        if es.date() == d:
            day_events.append((es, ee, e))
            total += ee - es
    day_events.sort()
    back_to_back = []
    for (s1, e1, a), (s2, e2, b) in zip(day_events, day_events[1:]):
        gap = (s2 - e1).total_seconds() / 60
        if gap < cfg["buffer_minutes"]:
            back_to_back.append({"first": a["title"], "second": b["title"],
                                 "gap_minutes": int(gap)})
    hours = total.total_seconds() / 3600
    _out({"ok": True, "date": d.isoformat(),
          "meeting_count": len(day_events),
          "total_meeting_hours": round(hours, 1),
          "over_limit": hours > cfg["max_daily_meeting_hours"],
          "limit_hours": cfg["max_daily_meeting_hours"],
          "insufficient_buffers": back_to_back})



def cmd_upcoming(args):
    """列出未来 N 小时内开始的日程,供定时提醒任务调用。"""
    db = _load()
    cfg = db["config"]
    now = _now(cfg)
    horizon = now + timedelta(hours=args.within)
    items = []
    for e in _active_events(db):
        es, ee = _event_times(e, cfg)
        if now <= es <= horizon:
            mins = int((es - now).total_seconds() / 60)
            items.append({"id": e["id"][:8], "title": e["title"],
                          "start": e["start"], "end": e["end"],
                          "type": e.get("type"), "location": e.get("location", ""),
                          "starts_in_minutes": mins})
    items.sort(key=lambda x: x["start"])
    _out({"ok": True, "now": _fmt(now), "within_hours": args.within,
          "count": len(items), "events": items})


def cmd_config(args):
    db = _load()
    if args.set:
        for pair in args.set:
            k, _, v = pair.partition("=")
            if k not in DEFAULT_CONFIG:
                _err(f"未知配置项: {k}", valid_keys=list(DEFAULT_CONFIG))
            cur = DEFAULT_CONFIG[k]
            if isinstance(cur, int):
                v = int(v)
            elif isinstance(cur, list):
                v = json.loads(v)
            db["config"][k] = v
        _save(db)
    _out({"ok": True, "config": db["config"], "data_file": DATA_FILE})

# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="Hermes Secretary calendar engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新增日程(自动冲突检测)")
    a.add_argument("--title", required=True)
    a.add_argument("--start", required=True, help="YYYY-MM-DD HH:MM")
    a.add_argument("--end")
    a.add_argument("--duration", type=int, help="分钟数(与 --end 二选一,默认30)")
    a.add_argument("--attendees", help="逗号分隔")
    a.add_argument("--type", default="internal",
                   choices=["internal", "external", "board", "investor", "personal", "travel", "focus"])
    a.add_argument("--priority", default="normal", choices=["low", "normal", "high", "critical"])
    a.add_argument("--location")
    a.add_argument("--notes")
    a.add_argument("--force", action="store_true", help="忽略冲突强制创建(需用户明确授权)")
    a.set_defaults(func=cmd_add)

    c = sub.add_parser("check", help="检查某时段是否可用(不创建)")
    c.add_argument("--start", required=True)
    c.add_argument("--end")
    c.add_argument("--duration", type=int)
    c.add_argument("--exclude-id", dest="exclude_id")
    c.set_defaults(func=cmd_check)

    g = sub.add_parser("agenda", help="查看日程")
    g.add_argument("--date")
    g.add_argument("--from", dest="from")
    g.add_argument("--to")
    g.set_defaults(func=cmd_agenda)

    f = sub.add_parser("free", help="搜索可用空档")
    f.add_argument("--duration", type=int, required=True)
    f.add_argument("--from", dest="from", required=True)
    f.add_argument("--to")
    f.add_argument("--max-slots", type=int, default=5)
    f.set_defaults(func=cmd_free)

    m = sub.add_parser("move", help="改期")
    m.add_argument("--id", required=True)
    m.add_argument("--start", required=True)
    m.add_argument("--end")
    m.add_argument("--reason")
    m.add_argument("--force", action="store_true")
    m.set_defaults(func=cmd_move)

    x = sub.add_parser("cancel", help="取消(软删除,保留历史)")
    x.add_argument("--id", required=True)
    x.add_argument("--reason")
    x.set_defaults(func=cmd_cancel)

    s = sub.add_parser("show", help="查看单条日程详情")
    s.add_argument("--id", required=True)
    s.set_defaults(func=cmd_show)

    l = sub.add_parser("load", help="单日负荷分析")
    l.add_argument("--date", required=True)
    l.set_defaults(func=cmd_load_analysis)

    u = sub.add_parser("upcoming", help="未来N小时内的日程(供定时提醒)")
    u.add_argument("--within", type=int, default=24, help="小时数,默认24")
    u.set_defaults(func=cmd_upcoming)

    cf = sub.add_parser("config", help="查看/修改配置")
    cf.add_argument("--set", nargs="*", help="key=value")
    cf.set_defaults(func=cmd_config)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
