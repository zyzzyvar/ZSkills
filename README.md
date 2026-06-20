# ZSkills

Agent Skills 集合,供 Hermes 加载使用。每个子目录为一个独立 skill。

| Skill | 说明 |
|---|---|
| [hermes-secretary](hermes-secretary/) | CEO 日程管理:自建日历引擎,排会/改期/冲突处理/空档搜索/差旅订票协作(任务台账),经验秘书式工作守则 |
| [astock-tracker](astock-tracker/) | A股持仓与关注股跟踪:盘前/午间/盘后三次分析,资金面/技术面/基本面/政策面交叉研判,主力手法识别,客观分析与操作建议分开(基于 AKShare 数据) |

## 在 Hermes 上安装

将本仓库克隆到 skills 目录(以 Claude Code 默认路径为例):

```bash
git clone https://github.com/zyzzyvar/ZSkills.git ~/.claude/skills/ZSkills
```

或只取单个 skill:

```bash
git clone --depth 1 https://github.com/zyzzyvar/ZSkills.git /tmp/zs \
  && cp -r /tmp/zs/hermes-secretary ~/.claude/skills/hermes-secretary
```

更新:在 skills 目录内执行 `git pull` 即可同步最新规则。

各 skill 的部署验收清单见其目录下的 `TESTING.md`。
