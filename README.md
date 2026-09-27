# 美团骑行历史导出与回放

把美团骑行历史导出成结构化数据，再渲染成竖屏回放视频。

分两个阶段，靠 `data/` 目录交接：

```
HAR  ──►  ridedata  ──►  data/  ──►  ridevideo  ──►  mp4
         抓取 + 整理               MapKit + Pillow + ffmpeg
```

两个包各自独立：`ridevideo` 只读数据目录，不 import `ridedata` 任何代码
（有测试守着这条边界）。设计细节见 [docs/architecture.md](docs/architecture.md)。

## 环境要求

- macOS —— 渲染依赖 MapKit、CoreLocation 和系统 PingFang 字体，还需要 `swiftc`
- Python 3.11+
- `ffmpeg`

只用抓取和整理功能的话，任何平台都能跑。

## 安装

用 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
```

装好后有两个命令，用 `uv run` 调用：

```bash
uv run ride-data --help    # 抓取与整理
uv run ride-video --help   # 渲染
```

不用 uv 也可以：

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e .
```

## 快速开始

唯一需要准备的是一份 HAR：在浏览器开发者工具的网络面板打开记录，
访问美团骑行历史页面，随便点开一两个订单，然后导出 HAR。

```bash
# 抓一天并出视频
uv run ride-data fetch 2025-04-04 --har capture.har
uv run ride-video 2025-04-04
```

抓多年历史并批量出视频：

```bash
uv run ride-data bulk --har capture.har          # 抓列表，按规则挑日期抓详情
uv run ride-video --all-days --workers 5         # 每个入选日期一个视频
```

没有账号也可以先看效果，仓库带一份合成样例：

```bash
RIDE_DATA_DIR=examples/data uv run ride-video 2025-01-01
```

## 筛选规则

抓详情要按订单逐笔请求，所以先挑日期。规则写在 `filters.yaml` 里，
不是写死在代码里：

```yaml
rules:
  real-gps:
    description: 当天至少有一笔含真实 GPS 轨迹
    has_full_track: true
```

```bash
uv run ride-data bulk --list-filters              # 看所有规则
uv run ride-data bulk --har capture.har --filter real-gps
uv run ride-data select --filter all-gps          # 详情已在本地，换规则不重抓
```

内置规则包括 `long-rides`（默认，任一单程超 30 分钟）、`daily-total`、
`real-gps`、`all-gps`、`connected`、`all`。可用条件有时长、订单数、
轨迹来源、星期、日期区间等，完整列表见 `filters.yaml` 的注释或
`ride-data bulk --list-filters`。

`select` 换规则时，上一次的结果会归档到 `data/three-years/archive/<规则名>/`，
两种口径可以对照。

## 凭证与隐私

**这个仓库不含任何真实数据。** `data/`、`input/` 和 `*.har` 都在 `.gitignore` 里。

- HAR 文件带登录凭证，**不要提交或分享**
- 导出的 JSON 含真实订单号、车辆号和完整 GPS 轨迹，也就是你的出行位置史
- 凭证只从 HAR 里读，绝不会写进任何输出文件
- HAR 的短期签名会过期，过期后接口返回 403，重新导出一份即可

接口定义（URL、请求体、分页、重试）全在 `src/ridedata/api.py` 里，
HAR 只贡献 `Cookie` 和 `userid` / `yoda*` 这几个头。

## ride-data

| 命令 | 作用 |
|---|---|
| `fetch <日期> --har X` | 抓某一天的订单和轨迹详情 |
| `bulk --har X` | 抓多年历史，按规则挑日期抓详情 |
| `select` | 用已有详情缓存换一套筛选规则 |

细节见 [docs/data.md](docs/data.md)。

## ride-video

**一天就是一个视频。** 当天连得上的骑行段在片中自然接起来，连不上的
（相隔超过 2 公里）标成「转场」，一天的记录不会被丢掉。

```bash
uv run ride-video 2026-05-04                # 单天
uv run ride-video --all-days --workers 5    # 全部入选日期
```

产出到 `data/video-out/`。1080×1920 / 30fps / H.264，时长按内容自适应
（约 14–55 秒）。细节见 [docs/video.md](docs/video.md)。

## 轨迹真实性

详情接口只对**部分**订单返回完整 `trackPoints`。每段骑行都会标明来源：

- **`gps`** —— 返回了 2 个以上轨迹点，画的是真实记录的轨迹
- **`inferred`** —— 只有首尾两点，路径用 MapKit 导航推算补全

MapKit 没有骑行模式，所以推算优先用**步行**（走的是自行车实际会走的路网，
也不受单行道限制），只有长途城际段找不到步行路线时才回落到
**驾车 + 避开高速 + 避开收费站**——默认驾车会把自行车导上高速和收费站。

含推算路径的视频会在画面底部标注「部分路径为地图导航推算，非 GPS 原始轨迹」。
推算路径不是真实轨迹。

## 开发

```bash
uv sync --group dev
uv run pytest          # 122 个测试
uv run ruff check src tests
```

两个环境变量可覆盖路径：`RIDE_DATA_DIR`（数据目录）、`RIDE_FILTERS`（规则文件）。

## 结构

```
src/
├── ridedata/            抓取 + 整理
│   ├── api.py           接口定义 + HAR 凭证提取
│   ├── fetch.py         单日抓取
│   ├── bulk.py          多年批量抓取 + 连接检测
│   ├── filters.py       筛选规则引擎
│   ├── prepare/         selection（换筛选规则）
│   ├── common.py        时间/距离/坐标/读写
│   └── paths.py         目录布局
└── ridevideo/           渲染
    ├── cli.py
    ├── swift/           MapKit 快照、路径推算、逆地理
    ├── rides/           load / basemap / camera / frame / render / batch
    ├── format.py        中文时长与时钟
    ├── swiftkit.py      按需编译 Swift + JSON 缓存
    └── paths.py         目录布局
filters.yaml             筛选规则
docs/architecture.md     设计说明
```

## License

MIT
