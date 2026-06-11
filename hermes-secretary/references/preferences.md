# 偏好规则说明

所有偏好以日历引擎的 config 为准,查看与修改:

```bash
python3 scripts/hcal.py config
python3 scripts/hcal.py config --set buffer_minutes=20 work_start=08:30
```

| 配置项 | 默认值 | 含义 |
|---|---|---|
| timezone | America/Los_Angeles | 老板所在时区,所有时间均以此为准 |
| work_start / work_end | 09:00 / 18:00 | 工作时间窗口 |
| lunch_start / lunch_end | 12:00 / 13:00 | 午餐保护时段,默认不排会 |
| buffer_minutes | 15 | 会议间最小缓冲 |
| max_daily_meeting_hours | 6 | 单日会议总时长告警线 |
| work_days | 周一至周五 | 工作日(Monday=0) |
| friday_internal_only_after | 13:00 | 周五此时间后仅内部事务 |

## 修改偏好的流程

老板在对话中表达新偏好时(例:"以后下午 5 点后别排会"):
1. 复述确认:"明白,以后工作时间截止到 17:00,我现在更新设置?"
2. 得到确认后执行 `config --set work_end=17:00`
3. 告知:"已更新并永久生效。"

不要只在当次对话中"记住"偏好——必须写入 config,否则下次会话就丢失了。

## 涉及多时区的会议

对方在其他时区时:始终以老板的时区排程和汇报,但在沟通文案中
同时写明双方时区时间(例:"北京时间周四 01:00 / 太平洋时间周三 10:00"),
换算用 Python 脚本验证,不要心算:

```bash
python3 -c "
from datetime import datetime
from zoneinfo import ZoneInfo
t = datetime(2026, 6, 18, 10, 0, tzinfo=ZoneInfo('America/Los_Angeles'))
print(t.astimezone(ZoneInfo('Asia/Shanghai')))"
```
