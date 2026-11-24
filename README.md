# 港口滞港费复算台

港口箱管结算员处理免堆日历与阶梯费率的本地工作台。样例、规则和复核记录都在项目内，不依赖外部服务。

## 启动

```bash
docker compose up --build
```

打开 `http://127.0.0.1:${PORT:-18520}`。默认端口见 `docker-compose.yml`。

## 验证

```bash
docker compose exec -T app pytest -q
docker compose config --quiet
```

## 操作

1. 在左侧选阶段，查看采信、整理、计算、判定和对账结果。
2. 选择 `dirty` 样例观察坏行和隔离原因。
3. 换 `clean` 样例执行批次，逐趟核对复算费用与港方票据；票据可来自 `sample_data/tickets_{profile}.csv`，也可在复核区粘贴。
4. 填写复核意见后签发；晚到票据用「载入晚到票据」并入，归属变化时旧复核自动失效。
5. 下载证据 JSON，核对来源摘要、票据归属、指标和签发版本。

业务规则见 `docs/BUSINESS-RULES.md`，字段定义见 `docs/DATA-DICTIONARY.md`。
