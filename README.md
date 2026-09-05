# HealthHub — 家庭健康数据标准化服务

独立部署的健康数据管理服务。**不管爬取、不管解析**（上游服务负责），本服务专注：

- **数据标准化**：术语映射（别名表，规则驱动）、单位换算、参考范围解析、确定性打标 —— 不用大模型兜底
- **存储**：多成员档案、原始数据全留档、微生物培养/药敏独立模型
- **查询**：指标检索、趋势序列、培养记录
- **参考建议**：GLM（智谱 Anthropic 兼容端点）按需分析 + 每日定时简报 —— **仅供参考，不构成医疗建议**

## 快速开始（Docker）

```bash
cd healthhub
cp compose.env compose.env   # 编辑：填入 LLM_API_KEY
docker compose up -d --build
curl http://localhost:8000/healthz
```

首次导入历史 CSV（挂载或复制到 `./data/seed_csv/`）：

```bash
docker compose exec api python -m app.csv_import /data/seed_csv/health_data_20260819-23.csv \
  --person-ref father --person-name 王维首 --sex M --birth 1969-01-01
```

重复执行安全（幂等，零新增）。

> **重要**：容器固定 `--workers 1`。进程内调度器 + SQLite WAL 依赖单写进程；扩容时先把调度器拆出进程。

## 建档与推送数据

```bash
# 建成员档案
curl -X POST :8000/persons -H 'Content-Type: application/json' \
  -d '{"external_ref":"father","name":"王维首","sex":"M","birth_date":"1969-01-01"}'

# 推送一份报告（上游解析服务的输出契约）
curl -X POST :8000/ingest/reports -H 'Content-Type: application/json' -d '{
  "person_ref": "father",
  "external_id": "hosp-20260823-0741-CBC",     // 幂等键：同 id 同内容重推无副作用，同 id 不同内容 409
  "hospital": "淮南市第一人民医院",
  "panel": "血常规",
  "sampled_at": "2026-08-23T07:41:00+08:00",
  "items": [
    {"code":"WBC","name":"白细胞","value":"6.80","unit":"10^9/L","ref_range_text":"3.5-9.5","flag":""},
    {"code":"PCT","name":"血小板压积","value":"0.10","unit":"%","ref_range_text":"0.17-0.39","flag":"↓"}
  ],
  "cultures": [{
    "specimen":"胆汁(引流液)","organism":"屎肠球菌","mdr_text":null,
    "susceptibilities":[{"drug_code":"DAP","drug_name":"达托霉素","result":"S","mic_text":"MIC=4"}]
  }]
}'
```

## 查询

```bash
GET /observations?person_id=1&code=CREA&from=2026-08-19&flag=abnormal
GET /trends/CREA?person_id=1          # 时间序列 + 首末值 + 变化幅度
GET /cultures?person_id=1             # 培养与药敏
GET /persons / GET /persons/{id}/counts
```

## 大模型功能

```bash
POST /insights {"person_id":1,"date_from":"2026-08-19","date_to":"2026-08-24","topic":"感染趋势"}
GET  /reports/daily?date=2026-08-23&person_id=1     # 无则现场生成；当日无数据返回 skipped
POST /reports/daily/generate?date=2026-08-23&person_id=1   # 强制重新生成
```

每日定时简报由 APScheduler 在 `DAILY_REPORT_TIME`（APP_TZ 时区）对所有有当日数据的成员生成；24h 无数据自动跳过（status=skipped，不烧 token）。

## 术语映射工作流（规则优先，大模型只提议）

无法解析的术语进入队列，**绝不猜测**：

```bash
GET  /mappings/unresolved                    # 查看待处理项（含样本值上下文）
POST /mappings/suggest {"unresolved_id":1}   # GLM-4.5-air 提议（仅建议，不落规则）
POST /mappings {"raw_code":"hs-CRP","raw_name":"超敏C反应蛋白","canonical_code":"CRP"}  # 人工确认
POST /admin/reprocess                        # 用新别名回填历史未解析数据
```

### 已内置的冲突消解

| 冲突 | 处理 |
|---|---|
| `PCT` = 血小板压积(%) vs 降钙素原(ng/ml) | 拆 `PCT_PLT` / `PCT_PROCAL`，按单位+panel 判别 |
| `CKMB` 酶法(U/L) vs 质量法(ng/ml) | 拆 `CKMB_ENZ` / `CKMB_MASS`，按单位判别 |
| 名称"白细胞" 血(WBC) vs 尿(LEU) | 按 panel 判别 |
| `A/G`、`PT%`、`NT-proBNP`、`Anti-TP` 等 | 显式别名 |

标准码为内部稳定码（`WBC`、`CREA`、`PCT_PROCAL`…），LOINC 仅在高置信度时作为附加元数据。

### 双 flag 语义

`flag_source` 保留化验单原始标记（↑↓/阴阳/S/R），`flag_computed` 按参考范围确定性重算（HIGH/LOW/ABNORMAL_CAT/NORMAL，无规则时为 NULL 不瞎标）。二者**永不合并**——乙肝表面抗体"阳性"是好事，数值高低与定性判读语义不同，提示词中已向大模型说明。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_PATH` | `/data/healthhub.db` | SQLite 路径（挂载卷持久化） |
| `LLM_BASE_URL` | `https://open.bigmodel.cn/api/anthropic` | Anthropic 兼容端点 |
| `LLM_MODEL_MAIN` / `LLM_MODEL_LIGHT` | GLM-5.3 / GLM-4.5-air | 建议用主模型 / 提议用轻量模型 |
| `LLM_API_KEY`（或 `ANTHROPIC_AUTH_TOKEN`） | 空 | 未配置时 insights/suggest 返回 503，其余功能不受影响 |
| `APP_TZ` | Asia/Shanghai | 日报"当天"边界与时区显示 |
| `DAILY_REPORT_TIME` | 07:30 | 每日简报时间（APP_TZ） |
| `INGEST_TOKEN` | 空 | 设置后所有写接口要求 `Authorization: Bearer <token>` |

## 运维

- **备份**：SQLite WAL 模式下安全备份 `sqlite3 /data/healthhub.db ".backup '/data/backup.db'"`
- **日志**：`docker compose logs -f api`
- **测试**：本地 `python -m pytest tests/`（22 项，含"真实 CSV 100% 术语可解析"与数据面板）
- **数据目录**：全部状态在单个 SQLite 文件 + 容器 `/data` 卷，迁移=复制文件

## 生产部署（47.97.101.194:8888）

服务器内存较小（1.6G），采用"本地构建镜像 → 传输 → 服务器 load"方式，避免在服务器上构建：

```bash
# 本地
docker compose build
docker save healthhub:latest | gzip > dist/healthhub-image.tar.gz
scp dist/healthhub-image.tar.gz compose.env root@47.97.101.194:/opt/healthhub/

# 服务器（首次已装 docker.io；注意 /opt/healthhub/data 必须 chown 1000:1000，容器以非 root 运行）
cd /opt/healthhub && gunzip -c healthhub-image.tar.gz | docker load
docker run -d --name healthhub -p 8888:8000 --env-file compose.env \
  -v /opt/healthhub/data:/data --restart unless-stopped healthhub:latest
docker exec healthhub python -m app.csv_import /data/seed_csv/health_data_20260819-23.csv
```

- 面板（免认证，按数字 ID 访问，王维首=2）：`http://47.97.101.194:8888/dashboard/2`；成员列表 `/`（含各 ID）
- 更新版本：本地重新 build+save+scp → 服务器 `docker load && docker rm -f healthhub && docker run ...`（数据在 /opt/healthhub/data 不受影响）
- 当前未设置 INGEST_TOKEN（按需开启写接口保护）

## 目录结构

```
app/            # FastAPI 应用（标准化引擎 normalize.py 为核心）
seed/           # 术语/别名/换算/参考范围 JSON（可追加，启动时幂等加载）
tests/          # pytest
data/           # SQLite（宿主机 ./data ↔ 容器 /data）
Dockerfile / docker-compose.yml / compose.env
```

## MCP 写入通道（v0.2.1）

`/mcp/`（Streamable HTTP）暴露写工具，鉴权同 INGEST_TOKEN（Bearer）。设计上由 Alterego 作为 MCP client 挂载（`ALTEREGO_MCP_SERVERS_JSON`），写路径统一收编进 Alterego 的鉴权体系；面板/查询仍走 REST。

- `ingest_report(report)` — 推检验报告，幂等语义同 REST
- `create_person(person)` — 建档，重复 external_ref 返回 409
- `confirm_mapping(mapping)` — 人工确认术语别名

构建（国内网络）：
```
docker build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ -t healthhub:latest .
```
测试：`python -m pytest tests/test_mcp.py -q`（注意 test_csv_* / test_ingest 的 CSV 用例依赖本机 `F:\health\data\` 路径，非 Windows 环境跳过属预期）
