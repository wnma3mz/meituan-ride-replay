# 抓取与整理

## 准备 HAR

唯一需要准备的输入。在浏览器开发者工具的网络面板开始记录，
访问美团骑行历史页面，随便点开一两个订单看详情，然后导出 HAR。

HAR 里带登录凭证，不要上传或分享。代码只从里面读 `Cookie` 和
`userid` / `yoda*` 这几个头，接口 URL、请求体、分页逻辑都写在
`src/ridedata/api.py` 里。

HAR 的短期签名会过期，过期后接口返回 403，重新导出一份即可。

## 单日抓取

```bash
uv run ride-data fetch 2025-04-04 --har capture.har
```

默认连订单详情一起抓（轨迹就在详情里）。只要列表不要轨迹可以加
`--skip-details`。

输出：

- `data/rides-YYYY-MM-DD.json` —— 汇总、接口全部字段、北京时间和时长等派生字段
- `data/rides-YYYY-MM-DD.csv` —— 便于表格软件查看的版本
- `data/raw/YYYY-MM-DD/page-*.json` —— 列表接口每页原始响应
- `data/raw/details/YYYY-MM-DD/*.json` —— 每笔详情的原始响应

详情带来的字段：`startLonlat`、`endLonlat`、`trackPoints`、`trackPointCount`、
`rideMiles`、`showTrack`，以及完整的 `detailBizData`。

## 多年批量抓取

```bash
uv run ride-data bulk --har capture.har
```

先把历史列表全部抓下来缓存，再按筛选规则挑出日期，只对这些日期抓详情
（详情要按订单逐笔请求，所以值得先筛）。

若 `data/three-years/all-orders.json` 已覆盖同一日期范围，默认复用缓存，
需要重新请求时加 `--refresh-history`。

常用参数：

| 参数 | 说明 |
|---|---|
| `--filter <规则名>` | 选哪些日期抓详情，默认 `long-rides` |
| `--list-filters` | 列出可用规则后退出 |
| `--years N` | 往前抓几年，默认 3 |
| `--from-date` / `--to-date` | 直接指定日期区间 |
| `--refresh-history` | 忽略列表缓存重新抓 |
| `--sleep-seconds` | 请求间隔，默认 0.25 秒 |
| `--detail-retries` | 详情失败重试次数，默认 3 |

输出到 `data/three-years/`：

| 文件 | 内容 |
|---|---|
| `all-orders.json/csv` | 全量订单和分页缓存索引 |
| `summary.json` | 覆盖日期、订单数、筛选规则及其完整条件、入选日期 |
| `qualifying-days.csv` | 入选日期汇总表 |
| `connected-groups.csv` | 自动识别出的连续订单组 |
| `days/YYYY-MM-DD.json/csv` | 每个入选日期的全部订单详情和连接候选 |
| `details/YYYY-MM-DD/` | 该日期每笔详情接口的原始响应 |

### 连接候选规则

前一笔订单结束点到后一笔起点直线距离不超过 500 米，且时间间隔在 0–2 小时内。
这只是自动筛选线索，**不把直线距离当作真实道路轨迹**。

## 筛选规则

规则写在项目根目录的 `filters.yaml` 里。一条规则里的多个条件是「与」关系，
全部满足才选中。文件里定义的规则会覆盖同名内置规则。

```yaml
rules:
  long-rides:
    description: 至少一笔单程骑行超过 30 分钟
    any_order_duration_min: 30

  weekend-gps:
    description: 周末且有真实轨迹
    weekday: [6, 7]
    has_full_track: true
```

可用条件：

| 条件 | 含义 |
|---|---|
| `any_order_duration_min` | 任一单程时长超过 N 分钟 |
| `total_duration_min` | 当日累计时长超过 N 分钟 |
| `max_total_duration_min` | 当日累计时长不超过 N 分钟 |
| `min_orders` / `max_orders` | 当日订单数下限 / 上限 |
| `min_connections` | 连续骑行组数量下限 |
| `has_full_track` | 当天是否至少有一笔含真实 GPS 轨迹 |
| `all_full_track` | 当天是否每一笔都含真实 GPS 轨迹 |
| `min_gps_orders` | 含真实轨迹的订单数下限 |
| `weekday` | ISO 星期，1=周一 7=周日，可给列表 |
| `date_from` / `date_to` | 日期区间 |

写错条件名会在加载时报错，不会静默匹配所有日期。

**时序限制**：`has_full_track`、`all_full_track`、`min_gps_orders` 依赖轨迹信息，
而轨迹要抓详情才知道。所以这三个条件在 `bulk` 阶段选不出日期，
必须用 `select`（那时详情已在本地）。

规则文件位置可用 `RIDE_FILTERS` 环境变量覆盖，或用 `--filters-file` 指定。

## 换一套筛选规则

详情抓过之后换口径不需要重新请求接口：

```bash
uv run ride-data select --filter real-gps
uv run ride-data select --list-filters
```

上一次的结果会归档到 `data/three-years/archive/<上次规则名>/`，
便于两种口径对照。归档目录名可用 `--archive-name` 指定。

换完规则直接 `ride-video --all-days` 就行，不需要额外的整理步骤。

## 交给渲染

`days/YYYY-MM-DD.json` 就是渲染的输入，一个文件一个视频：

```bash
uv run ride-video --all-days --workers 5
```

早先这里还有 `groups` 和 `manifest` 两步：前者把「连续订单组」导成独立目录，
后者生成任务清单。两者都已删除，因为按天出片之后它们没有消费者了，
而且 `groups` 只导出连成组的订单——49 天里有 20 天的骑行从不连续
（包括一笔 92 分钟的长途），那些天一个视频都产不出来。

连接信息仍然保留在 `days/*.json` 的 `connections` 和
`summary.connectedGroups` 里，渲染时用它决定哪两段之间标「转场」。

## 已知的数据特性

- `distanceMeter` 和 `rideMiles` 字段都是 null
- `carbonEmissionsGram` 有值，但按最紧约束反解出的 g/km 呈离散分档，
  疑似按时长分段估算而非真实里程，**不可当里程用**
- 只有部分订单的详情返回完整 `trackPoints`；其余只有首尾两点，
  视频侧会标为 `inferred` 并用导航推算补全
