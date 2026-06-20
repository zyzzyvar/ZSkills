# fetch.py 数据字段说明

## snapshot 输出结构
- `data.price`:
  - latest_close 最新收盘, pct_today 当日涨跌幅%, turnover_today 换手率%
  - ma5/ma10/ma20/ma60 均线
  - vol_ratio 量比(今日量/前5日均量)
  - macd: dif/dea/macd 及 trend(金叉/死叉趋势)
  - high_60d/low_60d 60日高低, pct_Nd N日累计涨跌幅
- `data.recent_klines`: 近 lookback 日的 date/close/pct/turnover/amount_yi(成交额亿元)
- `data.fund_flow`:
  - main_net_5d_wan / main_net_10d_wan 主力近5/10日净流入(万元)
  - trend 持续流入/持续流出/进出交织
  - recent 逐日明细
- `data.northbound`: 北向持股(部分标的无)
- `data.lhb_recent`: 近30日龙虎榜上榜次数及最新明细
- `data.comment`: 千股千评(机构参与度、综合评分等)
- `data.financials`: 最近2期财务摘要(营收/利润等)
- `unavailable`: 取数失败的维度列表

## market 输出
- indices: 上证/深证/创业板/科创50 的最新价与涨跌幅
- market_main_net_yi: 全市场主力净流入(亿元)
- northbound_summary: 北向资金当日概况

## 接口对照(AKShare)
- 历史K线: stock_zh_a_hist(adjust="qfq" 前复权)
- 个股资金流: stock_individual_fund_flow
- 大盘资金流: stock_market_fund_flow
- 指数: stock_zh_index_spot_em
- 北向汇总: stock_hsgt_fund_flow_summary_em
- 千股千评: stock_comment_detail_zhpj_lspf_em
- 个股新闻: stock_news_em
- 财务摘要: stock_financial_abstract_ths

## 单位约定
- 金额:个股资金流统一为"万元",大盘/北向为"亿元"
- 比率:涨跌幅、换手率、净占比均为百分数(已是%值,无需再×100)

## 接口稳定性说明
AKShare 数据源(东方财富/同花顺)偶有抖动或改版,接口可能临时失效。
fetch.py 已内置重试与降级:单维度失败会进 unavailable,不影响其他维度。
若某接口长期失效,通常是 akshare 版本滞后,`pip install -U akshare` 升级即可;
也可在 akshare 的 GitHub issues 查证接口现状。
