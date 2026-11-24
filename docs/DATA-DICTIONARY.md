# 字段定义

## 箱单来源

| 字段 | 类型 | 说明 |
|---|---|---|
| `container_id` | text | 箱号；与进出港日期共同确定趟次 |
| `gate_in` | date | 进港日期，业务字段，趟次身份的一部分 |
| `gate_out` | date | 出港日期，业务字段，趟次身份的一部分 |
| `free_days` | int | 业务字段 |
| `invoice_cents` | int | 业务字段 |

空字符串、`-`、`NA`、`null` 和 `未提供` 按空值处理。金额字段在整理层转为整数分或整数最小单位；日期按 ISO 日期解析；带时间的字段按本地时间解析。标识字段保持为文本，前导零不会被去除。

## 港方票据

票据来源与箱单来源相互独立，可以是 `sample_data/tickets_{profile}.csv`，也可以是 API 内联提交的对象数组。

| 字段 | 类型 | 说明 |
|---|---|---|
| `ticket_id` | text | 票据号，票据唯一身份；同一票号只入账一次 |
| `container_id` | text | 箱号 |
| `gate_in` | date | 可选；与 `gate_out` 同时给出或同时留空 |
| `gate_out` | date | 可选；与 `gate_in` 同时给出或同时留空 |
| `amount_cents` | int | 票据金额（整数分） |

- 票据表头与内联行支持港方常见列名：`票号`/`ticket_no`、`箱号`/`ctn_no`、`进港日期`、`出港日期`、`金额分`/`金额`。`金额` 按元折算为整数分。
- 只写日期中的一天按 `TICKET_PARTIAL_DATES` 隔离；出港早于进港按 `TICKET_GATE_ORDER` 隔离。
- 列数不符的行按 `TICKET_COLUMN_COUNT` 隔离，并计入票据来源行守恒。
