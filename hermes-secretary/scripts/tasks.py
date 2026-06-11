#!/usr/bin/env python3
"""
Hermes Secretary - 任务台账(零依赖)

跟踪"委托出去、等待回复"的异步任务(如:请秘书订机票)。
持久化存储,跨会话有效;秘书回复到达时凭任务编号或内容匹配。
存储: $HERMES_CALENDAR_DIR/tasks.json

用法:
  python3 tasks.py create --type flight_booking --summary "后天早上 北京→上海 机票" \
      --details "日期2026-06-13; 早上起飞; 经济舱; 单程" --assignee "王秘书"
  python3 tasks.py list                 # 默认列出所有未关闭任务
  python3 tasks.py list --all           # 含已关闭
  python3 tasks.py show --id T-a1b2
  python3 tasks.py note --id T-a1b2 --text "秘书已读,处理中"
  python3 tasks.py close --id T-a1b2 --result "CA1557 06-13 07:30 PEK T3 → 09:55 SHA 虹桥T2"
  python3 tasks.py cancel --id T-a1b2 --reason "行程取消"
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime

DATA_DIR = os.environ.get(
    "HERMES_CALENDAR_DIR",
    os.path.join(os.path.expanduser("~"), ".hermes-secretary"),
)
DATA_FILE = os.path.join(DATA_DIR, "tasks.json")


def _load():
    if not os.path.exists(DATA_FILE):
        return {"tasks": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(db):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _err(msg, **extra):
    _out({"ok": False, "error": msg, **extra})
    sys.exit(1)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _find(db, tid):
    tid = tid.lower().removeprefix("t-")
    matches = [t for t in db["tasks"] if t["id"].lower().removeprefix("t-").startswith(tid)]
    if not matches:
        _err(f"未找到任务 {tid!r}")
    if len(matches) > 1:
        _err("编号前缀匹配到多个任务,请提供更长前缀",
             candidates=[{"id": t["id"], "summary": t["summary"]} for t in matches])
    return matches[0]


def cmd_create(args):
    db = _load()
    task = {
        "id": "T-" + uuid.uuid4().hex[:4],
        "type": args.type,
        "summary": args.summary,
        "details": args.details or "",
        "assignee": args.assignee or "",
        "status": "open",            # open -> closed / cancelled
        "created_at": _now(),
        "linked_event_ids": [a.strip() for a in (args.link_events or "").split(",") if a.strip()],
        "result": "",
        "log": [{"at": _now(), "text": "任务创建"}],
    }
    db["tasks"].append(task)
    _save(db)
    _out({"ok": True, "task": task,
          "hint": "请在发给受托人的消息中带上编号 " + task["id"] + ",便于回复关联"})


def cmd_list(args):
    db = _load()
    tasks = db["tasks"] if args.all else [t for t in db["tasks"] if t["status"] == "open"]
    tasks = sorted(tasks, key=lambda t: t["created_at"], reverse=True)
    _out({"ok": True, "count": len(tasks), "tasks": tasks})


def cmd_show(args):
    db = _load()
    _out({"ok": True, "task": _find(db, args.id)})


def cmd_note(args):
    db = _load()
    t = _find(db, args.id)
    t["log"].append({"at": _now(), "text": args.text})
    _save(db)
    _out({"ok": True, "task": t})


def cmd_close(args):
    db = _load()
    t = _find(db, args.id)
    if t["status"] != "open":
        _err(f"任务已是 {t['status']} 状态")
    t["status"] = "closed"
    t["result"] = args.result
    t["log"].append({"at": _now(), "text": "完成: " + args.result})
    if args.link_events:
        t["linked_event_ids"] += [a.strip() for a in args.link_events.split(",") if a.strip()]
    _save(db)
    _out({"ok": True, "task": t})


def cmd_cancel(args):
    db = _load()
    t = _find(db, args.id)
    t["status"] = "cancelled"
    t["log"].append({"at": _now(), "text": "取消: " + (args.reason or "")})
    _save(db)
    _out({"ok": True, "task": t})


def main():
    p = argparse.ArgumentParser(description="Hermes Secretary task ledger")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("--type", default="general",
                   choices=["flight_booking", "hotel_booking", "train_booking",
                            "restaurant", "errand", "general"])
    c.add_argument("--summary", required=True)
    c.add_argument("--details")
    c.add_argument("--assignee")
    c.add_argument("--link-events", dest="link_events", help="关联日程ID,逗号分隔")
    c.set_defaults(func=cmd_create)

    l = sub.add_parser("list")
    l.add_argument("--all", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show")
    s.add_argument("--id", required=True)
    s.set_defaults(func=cmd_show)

    n = sub.add_parser("note")
    n.add_argument("--id", required=True)
    n.add_argument("--text", required=True)
    n.set_defaults(func=cmd_note)

    cl = sub.add_parser("close")
    cl.add_argument("--id", required=True)
    cl.add_argument("--result", required=True)
    cl.add_argument("--link-events", dest="link_events")
    cl.set_defaults(func=cmd_close)

    x = sub.add_parser("cancel")
    x.add_argument("--id", required=True)
    x.add_argument("--reason")
    x.set_defaults(func=cmd_cancel)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
