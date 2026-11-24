# 港口滞港费复算台深度登记

- 规模：中型
- 核心规则：免堆期从进港次日起跳过周末和法定假日，到期后按三档日费率复算。
- 样例：`sample_data/dirty.csv` 触发异常，`sample_data/clean.csv` 可完成签发；
  `sample_data/tickets_clean.csv`、`sample_data/tickets_dirty.csv` 是与之配套的港方票据。

## 功能模块

| 模块 | 动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 样例采信 | 定位坏行并做重复裁决 | `POST /api/stages/intake` | `app/intake/service.py` | `tests/test_intake.py` |
| M2 领域整理 | 解析字段与单位，隔离不满足前置条件的行 | `POST /api/stages/prepare` | `app/domain/prepare.py` | `tests/test_prepare.py` |
| M3 核心计算 | 执行项目核心规则并生成可比指标 | `POST /api/stages/calculate` | `app/domain/calculate.py` | `tests/test_calculate.py` |
| M4 规则判定 | 给出通过、复核或阻断结论 | `POST /api/stages/decision` | `app/domain/decision.py` | `tests/test_decision.py` |
| M5 运行对账 | 核对来源行、采信、隔离、重复、计算条目与逐趟票据 | `POST /api/stages/reconcile` | `app/reconcile/ledger.py`、`app/tickets/service.py` | `tests/test_reconcile.py`、`tests/test_tickets.py` |
| M6 批次复核 | 带版本号签发或暂缓，拒绝陈旧复核；晚到票据改归属使旧复核失效 | `POST /api/runs/{id}/review`、`POST /api/runs/{id}/tickets` | `app/review/service.py`、`app/review/store.py` | `tests/test_review.py`、`tests/test_review_invalidation.py` |
| M7 证据交付 | 仅在签发后输出可校验的复核证据 | `GET /api/runs/{id}/evidence` | `app/evidence/render.py` | `tests/test_evidence.py` |

## 输入陷阱

| 编号 | 处理位置 | 测试 |
|---|---|---|
| TRAP-02 | `app/intake/reader.py` | `tests/test_traps.py::test_bom_is_removed` |
| TRAP-03 | `app/intake/reader.py` | `tests/test_traps.py::test_quoted_newline_is_one_record` |
| TRAP-04 | `app/intake/service.py` | `tests/test_traps.py::test_repeated_header_is_ignored` |
| TRAP-05 | `app/intake/service.py` | `tests/test_traps.py::test_extra_column_is_isolated` |
| TRAP-06 | `app/intake/normalize.py` | `tests/test_traps.py::test_fullwidth_digits_are_canonical` |
| TRAP-07 | `app/intake/normalize.py` | `tests/test_traps.py::test_sentinel_is_blank` |
| TRAP-08 | `app/intake/service.py` | `tests/test_traps.py::test_leading_zero_is_preserved` |
| TRAP-14 | `app/intake/service.py` | `tests/test_traps.py::test_conflicting_duplicate_is_isolated` |
| TRAP-16 | `app/intake/reader.py` | `tests/test_traps.py::test_unclosed_quote_rejects_file` |
| TRAP-18 | `app/intake/reader.py` | `tests/test_traps.py::test_empty_and_header_only_differ` |
| TRAP-26 | `app/review/store.py` | `tests/test_review.py::test_stale_revision_is_rejected` |
| TRAP-27 同箱多趟 | `app/intake/service.py`、`app/domain/trip.py` | `tests/test_tickets.py::test_same_container_multiple_trips_are_not_a_conflict` |
| TRAP-28 同箱多趟旧票不认领 | `app/tickets/service.py` | `tests/test_tickets.py::test_container_only_ticket_is_ambiguous_for_multi_trip_container_even_if_amount_matches` |
| TRAP-29 日期票优先 | `app/tickets/service.py` | `tests/test_tickets.py::test_dated_ticket_claims_matching_trip_over_container_only_ticket` |
| TRAP-30 一票只入账一次 | `app/tickets/service.py` | `tests/test_tickets.py::test_two_dated_tickets_for_same_trip_conflict_and_block` |
| TRAP-31 晚到票据改归属 | `app/review/store.py` | `tests/test_review_invalidation.py::test_late_ticket_reassignment_invalidates_previous_review` |

## 深度边界

采信层区分编码、重复表头、列数、空值、全角字符、前导零与冲突重复，并按趟次而不是箱号去重；整理层负责单位和字段边界；计算层执行核心规则，同箱多趟各自复算；判定层保留原因码；对账层要求来源行等于采信、隔离、重复和忽略之和，并逐趟核对港方票据（日期票优先、旧票唯一才认领、一票只入账一次）；复核层使用版本号阻止陈旧签发，票据归属变更时旧批次的复核意见一并失效。
