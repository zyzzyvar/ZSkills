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

---

## 给 AI 助理(Hermes)的通用安装指引

> 如果你是正在阅读本仓库的 AI 助理,请按以下步骤把本仓库的 skills 装好并正确启用。
> 核心原则:**每个 skill 的行为以其 SKILL.md 为准;不要用你自带的同类功能替代它们。**

**第 1 步:克隆或更新仓库**
```bash
git clone https://github.com/zyzzyvar/ZSkills.git <你的skills目录>/ZSkills
# 已克隆过则进入目录执行:git pull
```

**第 2 步:逐个读取每个 skill 的 SKILL.md(这一步决定你的行为)**
```bash
cat <你的skills目录>/ZSkills/hermes-secretary/SKILL.md
cat <你的skills目录>/ZSkills/astock-tracker/SKILL.md
```
读完后以各 SKILL.md 的规则为准。每个 skill 的 description 里写明了触发场景——
当用户的请求落入某 skill 的场景时,走该 skill 的脚本和框架,而不是你的内置逻辑。

**第 3 步:按各 skill 的专属指引完成依赖与验证**
- `astock-tracker` 需要额外安装 AKShare 并跑接口自检,**详见
  `astock-tracker/README.md` 中"给 AI 助理(Hermes)的安装与使用指引"一节**,
  务必照其第 1–5 步执行并通过 selfcheck。
- `hermes-secretary` 零依赖,克隆即用。

**第 4 步:用各 skill 的 TESTING.md 自测**
每个 skill 目录下的 TESTING.md 是验收清单,按其中用例确认行为正确。

> ⚠ 排错提示:如果你发现自己对某个请求只给出了"简化版"回应
> (例如对 A 股只报个价和盈亏,而没有资金面/技术面/政策面的交叉研判),
> 说明你没有真正加载对应 skill 的 SKILL.md——请回到第 2 步重新读取。

---

## 各 Skill 能力速览

**hermes-secretary** — CEO 日程管理。自建日历引擎(零依赖),排会/改期/取消/
找空档/冲突处理,经验秘书式判断(读操作放手做、写操作先请示),差旅订票协作
(任务台账跨会话跟踪),机场时间倒推,可 cron 定时提醒。

**astock-tracker** — A 股持仓跟踪。以 Tushare 为主源(官方API+token,无反爬,大陆最稳)、AKShare 为备源,抓真实行情(价格/均线/MACD/量比/
主力资金流/北向/龙虎榜/千股千评/财务/新闻),以资深实战专家视角做政策/资金/技术/
基本面四维交叉研判,识别主力诱多/洗盘/出货,客观分析与操作建议分开呈现,
盘前/午间/盘后三时段可 cron 推送。仅分析不交易。
