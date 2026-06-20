# A股 Tracker

以资深A股实战专家视角,管理持仓股与关注股,每日盘前/午间/盘后三次跟踪分析:
基本面、资金面(主力/北向/龙虎榜)、技术面与主力手法、政策消息面,给出客观研判
与操作参考。数据来自 AKShare(免费、公开),分析由 Hermes(LLM)完成。

## 结构

```
astock-tracker/
├── SKILL.md                       # 专家分析框架与工作守则
├── scripts/
│   ├── portfolio.py               # 持仓/关注管理(零依赖)
│   ├── fetch.py                   # 数据采集引擎(需 akshare)
│   └── daily_brief.py             # 三时段批量采集(供 cron)
└── references/
    ├── analysis-framework.md      # 三时段方法论与指标解读
    ├── manipulation-patterns.md   # 主力诱多/洗盘/出货识别
    ├── policy-sectors.md          # 政策解读与板块对应
    └── data-fields.md             # 字段与接口对照
```

## 安装

```bash
# 1. 克隆(若已用 ZSkills 集合则跳过)
git clone https://github.com/zyzzyvar/ZSkills.git ~/.claude/skills/ZSkills

# 2. 安装数据依赖
pip install akshare --break-system-packages

# 3. 部署后自检接口连通性(关键!)
python3 ~/.claude/skills/ZSkills/astock-tracker/scripts/fetch.py selfcheck
```

`selfcheck` 应显示多数接口 OK。若大量失败,检查:网络能否访问 eastmoney.com /
同花顺(国内财经站),akshare 是否为较新版本(`pip install -U akshare`)。

## 数据存储

持仓与关注数据在 `~/.astock-tracker/portfolio.json`,每日采集的 brief 在
`~/.astock-tracker/briefs/`。可用环境变量 `ASTOCK_DIR` 重定向。建议定期备份
portfolio.json(里面是你的成本价等关键信息)。

## 定时三次推送(cron 配置)

数据采集与分析解耦:cron 在固定时间跑 `daily_brief.py` 采集数据落盘,
然后触发 Hermes 读取最新 brief 做分析并通过企微推送。

交易日时间参考(A股 9:30 开盘 / 11:30–13:00 午休 / 15:00 收盘):

```cron
# 盘前 8:40(开盘前定调)
40 8 * * 1-5 cd ~/.claude/skills/ZSkills/astock-tracker/scripts && python3 daily_brief.py --session pre >> ~/.astock-tracker/cron.log 2>&1

# 午间 11:40(上午收盘后)
40 11 * * 1-5 cd ~/.claude/skills/ZSkills/astock-tracker/scripts && python3 daily_brief.py --session noon >> ~/.astock-tracker/cron.log 2>&1

# 盘后 15:20(收盘后复盘)
20 15 * * 1-5 cd ~/.claude/skills/ZSkills/astock-tracker/scripts && python3 daily_brief.py --session post >> ~/.astock-tracker/cron.log 2>&1
```

`daily_brief.py` 跑完会输出 brief 文件路径。把"采集完成 → 通知 Hermes 分析推送"
这一步接到你的 Hermes 调度里(具体方式取决于你的部署:可以是 cron 调用 Hermes 的
消息接口,或 Hermes 轮询 briefs 目录发现新文件即分析)。节假日 A股休市,cron 用
`1-5` 仅工作日,但法定调休需自行处理(可在脚本前加交易日历判断)。

## 重要声明

本 skill 仅做数据跟踪与分析,**不执行任何实际交易**。所有分析不构成投资建议,
A股有风险,决策请独立判断。详见 SKILL.md。验收清单见 TESTING.md。
