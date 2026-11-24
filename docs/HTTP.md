# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿；请求体可带 `tickets` 数组做港方票据对账，缺省读取 `sample_data/tickets_{profile}.csv`。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `POST /api/runs/{id}/tickets`：并入晚到票据并重新对账，提交 `tickets` 与可选 `mode`（`merge` 按票号覆盖，`replace` 完整重发）；归属变化时旧批次复核失效。
- `GET /api/runs/{id}/evidence`：下载已签发证据；旧批次复核失效后返回 409。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。
