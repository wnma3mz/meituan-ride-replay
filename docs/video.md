# 骑行回放视频

把一天的骑行记录渲染成一条竖屏回放视频。1080×1920 / 30fps / H.264。

读 `data/` 里的 JSON，产出到 `data/video-out/`。

## 一天一个视频

粒度就是「天」，因为筛选规则挑出来的单位就是天。当天连得上的骑行段（相距 2 公里以内）在片中自然接起来，连不上的标成「转场」并把镜头拉远，说明这段不是骑过去的。一天的记录不会被丢掉。

早先的做法是只渲染「连续订单组」，结果 49 个入选日期里有 20 天一个视频都产不出来——那些天的骑行从不连续，包括一笔 92 分钟的长途。

## 用法

```bash
# 单天（读 data/three-years/days/ 或 data/rides-*.json）
uv run ride-video 2026-05-04

# 全部入选日期
uv run ride-video --all-days --workers 5
```

批量时附一份 `index.json` 汇总。

| 参数 | 说明 |
|---|---|
| `--ride-seconds` | 分配给骑行段的总时长；默认按内容自适应 |
| `--fps` | 默认 30 |
| `--output` / `--output-dir` | 输出位置 |
| `--workers` | 批量并行进程数，默认按 CPU 核数取 2–5 |
| `--overwrite` | 批量时重渲染已存在的视频 |
| `--quiet` | 只输出结果 JSON，供程序调用 |

时长按内容自适应：2 单短组约 14 秒，12 单大组约 55 秒。

## 画面

- 地图全屏铺满，MapKit 深色底图 + POI 关闭，信息以渐变遮罩浮层叠加
- 镜头：开场看全局轮廓 → 逐段推近跟随 → 结尾拉远出合计卡片
- 节奏按真实时长等比，空档压缩到 1.2 秒并显示「等待 N 分钟」；每段至少 2 秒，否则短途段的地名根本看不清
- 底部进度条每段宽度 = 该段真实时长占比，和画面时钟严格一致
- 上一段终点到下一段起点超过 2 公里时判为「转场」，镜头拉到全局，明说这段不是骑过去的

## 轨迹来源

每段标 `gps` 或 `inferred`，取决于详情接口给了多少 `trackPoints`：

- **`gps`**：返回了 2 个以上轨迹点，画的是真实记录的轨迹。近三年有 118 单属于这种。
- **`inferred`**：只给了首尾两点，用 MapKit 导航推算补全。画面底部显示「部分路径为地图导航推算，非 GPS 原始轨迹」。

推算优先用**步行模式**（`swift/route.swift`）：MapKit 没有骑行模式，而步行走的是自行车实际会走的路网，也不受单行道限制。只有长途城际段找不到步行路线时才回落到**驾车 + 避开高速 + 避开收费站**——默认驾车会把自行车放上京津塘高速并穿过两个收费站，必须避开。推算路径不是真实轨迹。

`route.swift` 输出实际用了哪种模式（`walking` 或 `driving-no-highway`），调用方按它标注，不能一律写成步行。

## 缓存

`src/ridevideo/cache/` 跨次复用，删掉会重新生成：

- `basemaps/`：MapKit 底图快照 + 轨迹点的像素投影。key 里带 `PROJECTION_VERSION`，改投影逻辑时把它 +1 即可让旧缓存失效
- `locations.json`：CoreLocation 中文地名
- `routes.json`：导航推算路径

## 依赖

- macOS（MapKit / CoreLocation / PingFang 字体），需要 `swiftc`
- `ffmpeg`
- Pillow

`src/ridevideo/bin/` 下的 Swift 二进制是首次运行时自动编译的产物，删掉会重新编译。

## 实现要点

底图渲染成 1.5 倍尺寸，镜头靠裁切+缩放推近（Ken Burns）：地图标签不会在缩放中途跳变，每帧约 0.1 秒而不是每帧重新请求快照。

注意 `snap.swift` 输出投影坐标前翻转了 y —— AppKit 原点在左下角，PIL 在左上角，不翻转会让整条路线南北镜像。

批量渲染以 `python -m ridevideo` 起子进程，每个 worker 渲一整条视频：MapKit 快照和 ffmpeg 本身已是多线程，所以并行数不必等于核数。

## 结构

```
cli.py           # CLI 入口
paths.py         # 目录布局（RIDE_DATA_DIR 可覆盖数据目录）
format.py        # 中文时长与时钟
swiftkit.py      # 按需编译 Swift + JSON 缓存
swift/
├── snap.swift   # MapKit 快照 + 像素投影
├── route.swift  # 步行优先、驾车兜底的路径推算
└── geo.swift    # CoreLocation 中文逆地理
rides/
├── load.py      # 读 JSON（按天或按组），解析轨迹，标 gps/inferred
├── basemap.py   # 超采样底图快照，并行 + 磁盘缓存
├── camera.py    # 时间轴（等比 + 空档压缩 + 自适应时长）、镜头裁切
├── frame.py     # Pillow 合成单帧
├── render.py    # 帧序列 → ffmpeg
└── batch.py     # 多进程批量
```
