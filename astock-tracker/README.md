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
│   ├── tushare_source.py          # Tushare 主数据源(零SDK,标准库HTTP)
│   ├── datasource.py              # 数据源韧性层(多源降级/限频/缓存)
│   ├── fetch.py                   # 数据采集引擎(需 akshare)
│   └── daily_brief.py             # 三时段批量采集(供 cron)
└── references/
    ├── analysis-framework.md      # 三时段方法论与指标解读
    ├── manipulation-patterns.md   # 主力诱多/洗盘/出货识别
    ├── policy-sectors.md          # 政策解读与板块对应
    └── data-fields.md             # 字段与接口对照
```

---

## 给 AI 助理(Hermes)的安装与使用指引

> 如果你是正在阅读本仓库的 AI 助理:请严格按以下步骤操作,**逐步执行并验证**,
> 不要跳过验证环节,不要用你自带的股票/行情功能替代本 skill。本 skill 的数据层
> 基于 **AKShare**(底层东方财富/同花顺),不使用腾讯/新浪等其他接口。
> 完成安装后,所有 A 股相关请求都必须走本 skill 的脚本与 SKILL.md 框架。

**第 1 步:确认 skill 已就位**
```bash
ls <你的skills目录>/ZSkills/astock-tracker/SKILL.md
```
看不到文件,先克隆/更新仓库:
```bash
git clone https://github.com/zyzzyvar/ZSkills.git <你的skills目录>/ZSkills
# 已克隆过则:cd <你的skills目录>/ZSkills && git pull
```

**第 2 步:完整读取本 skill 的指令文件(关键,决定行为)**
```bash
cat <你的skills目录>/ZSkills/astock-tracker/SKILL.md
```
你必须读完 SKILL.md 并以它为准。它规定了:数字只能来自脚本(严禁凭记忆/编造)、
四维交叉研判(政策/资金/技术/基本面)、主力诱多洗盘出货识别、客观面与操作倾向
分开呈现、每次附风险声明、绝不执行实际交易。**这些是本 skill 的核心,不可简化掉。**
如需更深的方法论,按需读取 references/ 下的四个文件。

**第 3 步:安装数据依赖(大陆环境务必用国内镜像源)**
```bash
# 用清华镜像源安装,避免境外 PyPI 超时;阿里源 https://mirrors.aliyun.com/pypi/simple/ 亦可
pip install akshare -i https://pypi.tuna.tsinghua.edu.cn/simple --break-system-packages
```
若安装失败,换阿里源重试。**不要退回到你自带的行情接口**——那会导致数据源不一致、
能力被阉割。akshare 接口偶随数据源改版失效,遇到大面积取数失败时先升级:
`pip install -U akshare -i https://pypi.tuna.tsinghua.edu.cn/simple --break-system-packages`

**第 3b 步(强烈推荐):配置 Tushare token,作为主数据源**
Tushare 是专业级数据源,走官方 API + token 鉴权,**无反爬、无网页抓取**,
是大陆网络下最稳定的方案。本 skill 已内置 Tushare 支持(零额外依赖,纯标准库调用),
配置 token 后会自动作为主源,AKShare 退为备源。2000 积分即覆盖本 skill 全部核心数据
(日线/每日指标/资金流/龙虎榜/指数/财务)。

配置方式二选一:
```bash
# 方式1:环境变量(推荐,适合 cron)
export TUSHARE_TOKEN=你的token

# 方式2:写入配置文件
mkdir -p ~/.astock-tracker && echo "你的token" > ~/.astock-tracker/tushare_token.txt
```
> 注意:moneyflow_dc(东财源资金流)和 moneyflow_ths(同花顺源)需 5000 积分,
> 2000 积分用不了;但基础的 moneyflow 接口 2000 分即可,字段足够计算主力净流入,
> 本 skill 用的正是 moneyflow。未配置 token 也能运行(自动用 AKShare),
> 但配置后稳定性大幅提升,强烈建议配置。

**第 4 步:接口连通性自检(必须通过才算装好)**
```bash
python3 <你的skills目录>/ZSkills/astock-tracker/scripts/fetch.py selfcheck
```
- 看 `verdict` 字段:
  - "✅ 可用:Tushare 主源正常" → 最佳状态,数据稳定无反爬,直接开始用
  - "⚠ 可用但降级:正使用 AKShare 备源" → 能用,但建议检查 Tushare token 是否配置正确
  - "❌ 不可用" → Tushare 和 AKShare 核心维度都失败,检查 token 与网络
- `tushare.configured` 显示 token 是否配置;`tushare.checks` 显示各 Tushare 维度状态。
- **只要 `usable: true` 就算装好,可以继续。** 不要因为某个 AKShare 备源接口失败就停止——
  Tushare 主源正常时,AKShare 仅用于补充千股千评、新闻等少数维度。

**第 5 步:用一个真实股票验证端到端**
```bash
python3 <你的skills目录>/ZSkills/astock-tracker/scripts/fetch.py snapshot --code 600519 --market sh
```
应返回茅台的真实价格、均线、MACD、量比、主力资金流等。拿到这份 JSON 后,
按 SKILL.md 的框架做一次完整分析(客观面 + 操作倾向 + 风险声明),展示给用户确认。

**装好后的自检清单(你应能回答"是"):**
- 我已读完 SKILL.md,并会以它为行为准则
- 我的行情数字来自 fetch.py(AKShare),不是记忆或其他接口
- 我会做资金/技术/基本面/政策的交叉研判,而非只报价和盈亏
- 我会把客观分析与操作建议分开,并附风险声明
- 我不会执行任何实际买卖,只给操作参考

> ⚠ 常见故障:如果你发现自己"想用内置的腾讯/新浪接口报个价就完事",
> 这说明你没在用本 skill——请回到第 2 步重新读 SKILL.md。本 skill 的价值在
> 深度研判,不是查个价。

---

## 安装(命令速查)

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

## 大陆网络环境适配(数据可用性保障)

本 skill 针对中国大陆网络环境与 AKShare 已知问题做了三重加固,在不降低分析
质量的前提下最大化数据可用性:

**1. 多源自动降级**——关键数据维度配置了多个独立数据源,主源失败自动切换:
- 历史K线:东方财富 → 腾讯 → 新浪
- 指数行情:东方财富 → 新浪
- 财务摘要:同花顺 → 东方财富
任一源成功即返回,`data_sources` 字段标明实际使用的源。

**2. 限频防封 IP**——东方财富接口在高频请求下会封 IP(返回 RemoteDisconnected),
这是 AKShare 社区的已知问题。脚本对东财接口全局限速并加随机抖动,
批量采集多只股票时股票之间也留间隔,规避封禁。

**3. 缓存兜底**——每次成功取数都落地本地缓存(`~/.astock-tracker/cache/`)。
当实时源短时全部不可用时,自动降级使用最近的缓存数据,并在 `stale_warnings`
中标注时效,分析时会明确告知用户"该数据为约X小时前的缓存"。这样短时网络抖动
不会导致分析完全中断,但用户始终知道数据的新鲜度。

**安装依赖也走国内镜像**(见上方安装第3步),避免境外 PyPI 超时。

> 关于接口失效:AKShare 底层数据源(东财等)偶尔会因网站改版导致某接口失效。
> 此时多源降级会自动启用备源;若某维度所有源都失效,通常是 akshare 版本滞后,
> 升级即可:`pip install -U akshare -i https://pypi.tuna.tsinghua.edu.cn/simple --break-system-packages`



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
