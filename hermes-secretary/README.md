# Hermes Secretary

一个自建日历的 CEO 日程管理 Agent Skill。无需任何外部日历服务:
日程数据存储为本地 JSON 文件,全部时间运算与冲突检测由零依赖的
Python 脚本完成,LLM 只负责秘书式的判断与沟通。

## 结构

```
hermes-secretary/
├── SKILL.md                          # 秘书工作守则(触发后加载)
├── scripts/hcal.py                   # 日历引擎(零依赖,Python 3.9+)
└── references/
    ├── preferences.md                # 偏好配置说明
    ├── meeting-types.md              # 会议类型默认规范
    └── communication-templates.md    # 改期/婉拒/邀请模板
```

## 安装

**方式一:直接给 Hermes 仓库链接**,让它克隆到 skills 目录:

```bash
git clone https://github.com/zyzzyvar/ZSkills.git ~/.claude/skills/ZSkills
```

(skills 目录按你的 Hermes 部署而定,Claude Code 默认为 `~/.claude/skills/`)

**方式二:下载 release 中的 `hermes-secretary.skill` 文件**,在支持
skill 安装的界面直接导入。

## 数据位置

日历数据默认在 `~/.hermes-secretary/calendar.json`,可通过环境变量
`HERMES_CALENDAR_DIR` 重定向。建议定期备份该文件;取消的日程为软删除,
完整历史可追溯。

## 快速验证

```bash
python3 scripts/hcal.py add --title "测试会议" --start "2026-06-15 14:00" --duration 60
python3 scripts/hcal.py agenda --date 2026-06-15
python3 scripts/hcal.py free --duration 60 --from 2026-06-15
```

完整验收清单见 [TESTING.md](TESTING.md)。
