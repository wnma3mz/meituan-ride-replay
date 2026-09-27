# 样例数据

`sample-day.json` 是一份**完全合成**的数据，坐标、订单号、时间都是编造的，
不含任何真实出行记录。用途是让没有美团账号的人也能跑通渲染、看到成片长什么样。

```bash
RIDE_DATA_DIR=examples/data uv run ride-video 2025-01-01
```

它的字段形状和 `ride-data fetch` 的真实输出一致，所以也可以当作
接口返回格式的参考。

真实数据请自己抓，产物会落在 `data/`，那个目录不会进仓库。
