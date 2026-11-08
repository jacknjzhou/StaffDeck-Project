# StaffDeck 备份与恢复手册

本文档覆盖生产部署（`packaging/docker/docker-compose.yml`）下 PostgreSQL 的备份、
恢复演练与真实恢复。应用本身无状态，不可重建的只有 PostgreSQL 数据卷 `pgdata`；
Redis 里的角色租约与缓存都会自动重建，不需要备份。

> **恢复是破坏性操作。** 恢复会覆盖目标库中的现有数据，且无法撤销。
> 任何恢复都必须先恢复到临时库验证，确认无误后再切换；不要直接对生产库执行
> `pg_restore` / `psql` 覆盖写。切换前先做一次「恢复前备份」。

## 1. 备份

### 1.1 手动备份

```bash
mkdir -p backups                       # 脚本不会替你创建目录
bash scripts/backup_db.sh              # 打印生成的备份路径
```

输出示例：`/srv/staffdeck/backups/staffdeck-2026-11-09-031500.sql.gz`。

脚本行为：

- 通过 `docker compose -f packaging/docker/docker-compose.yml exec -T postgres
  pg_dump -U staffdeck staffdeck | gzip` 生成逻辑备份；
- 先写 `.partial` 临时文件，`gzip -t` 校验通过后才改名，中途失败不会留下看似完整的备份；
- 默认保留 14 天，按文件名前缀 `staffdeck-*.sql.gz` 清理更早的备份；
- 备份目录不存在时直接退出（退出码 1），避免磁盘没挂上时把备份写进容器层。

可用环境变量覆盖默认值：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `STAFFDECK_BACKUP_DIR` | `<repo>/backups` | 备份目录，必须已存在 |
| `STAFFDECK_BACKUP_RETENTION_DAYS` | `14` | 保留天数 |

### 1.2 定时任务

cron 示例（每天 03:15 备份，日志留在 `/var/log/staffdeck-backup.log`）：

```cron
# m h dom mon dow command
15 3 * * * cd /srv/staffdeck && STAFFDECK_BACKUP_DIR=/srv/backups \
  /usr/bin/flock -n /tmp/staffdeck-backup.lock \
  /bin/bash scripts/backup_db.sh >> /var/log/staffdeck-backup.log 2>&1
```

- `flock -n` 防止上一轮备份未结束时叠加执行；
- cron 的 `PATH` 很短，`docker` 通常不在其中；若日志出现 `docker: command not found`，
  在 crontab 顶部补 `PATH=/usr/local/bin:/usr/bin:/bin`；
- 备份目录建议放在独立磁盘或远端挂载点，并与数据库卷分离：和 `pgdata` 同盘时，
  磁盘故障会同时带走生产库与备份。

### 1.3 备份有效性检查

```bash
latest=$(ls -1t backups/staffdeck-*.sql.gz | head -1)
gzip -t "$latest"                        # 完整性
gzip -dc "$latest" | head -20            # 应看到 pg_dump 头部注释
zcat "$latest" | grep -c "CREATE TABLE"  # 表数量应大于 0
```

备份文件包含全部业务数据与凭据密文，按生产数据等级保护（权限 `0600`、禁止进 Git）。

## 2. 恢复演练（推荐每月一次）

演练在临时库进行，不触碰生产库。全程在 `packaging/docker` 下执行 compose 命令。

### 2.1 暂停写入方

```bash
cd packaging/docker
docker compose stop api worker connector
```

只停应用容器，**保留** `postgres` 与 `redis`。停机窗口内前端不可用，演练应安排在维护窗口。

### 2.2 恢复到临时库

```bash
docker compose exec -T postgres createdb -U staffdeck staffdeck_restore
gzip -dc ../../backups/staffdeck-2026-11-09-031500.sql.gz \
  | docker compose exec -T postgres psql -U staffdeck -d staffdeck_restore
```

`pg_dump` 默认输出为纯 SQL，用 `psql` 回放；若日志里有若干 `ERROR: role ... does not
exist` 之类的告警，先确认是否影响数据（本部署由 `staffdeck` 单一角色持有全部对象）。

### 2.3 校验行数与结构

```bash
docker compose exec -T postgres psql -U staffdeck -d staffdeck_restore -c "
  SELECT 'users' AS table, count(*) FROM users
  UNION ALL SELECT 'sessions', count(*) FROM sessions
  UNION ALL SELECT 'messages', count(*) FROM messages;
"
```

再核对迁移版本与生产一致（恢复到旧备份时会落后，需要跑迁移补齐）：

```bash
docker compose exec -T postgres psql -U staffdeck -d staffdeck_restore \
  -c "SELECT version_num FROM alembic_version;"
```

验收口径：关键表行数与备份时点相符、`alembic_version` 与生产一致（或可由
`python -m app.db.migrations_cli upgrade` 补齐）、应用能连上临时库排查问题。

### 2.4 清理

```bash
docker compose exec -T postgres dropdb -U staffdeck staffdeck_restore
docker compose start api worker connector
```

## 3. 真实恢复（破坏性）

仅在确认生产库损坏且已有可用备份时执行。**第 1 步不可跳过**：先备份当前（哪怕已损坏的）
库，保留事后取证与二次尝试的机会。

1. 再次备份现状：

   ```bash
   bash scripts/backup_db.sh
   ```

2. 停止所有写入方（api 会跑迁移，必须先停）：

   ```bash
   cd packaging/docker
   docker compose stop api worker connector
   ```

3. 重建空库后回放备份。`pg_dump` 的纯 SQL 假设目标库为空，直接对非空库回放会在
   `CREATE TABLE` 处报错；因此先删库重建（本部署没有依赖额外扩展，重建后无需补装）：

   ```bash
   docker compose exec -T postgres dropdb -U staffdeck --force staffdeck
   docker compose exec -T postgres createdb -U staffdeck staffdeck
   gzip -dc ../../backups/staffdeck-2026-11-09-031500.sql.gz \
     | docker compose exec -T postgres psql -U staffdeck -d staffdeck
   ```

   删库这一步不可撤销——第 1 步的备份是唯一的退路。

4. 校验迁移版本与关键表行数（同 2.3），必要时补迁移：

   ```bash
   docker compose run --rm -e STAFFDECK_RUN_MIGRATIONS=1 api
   ```

   `api` 启动时若 `STAFFDECK_RUN_MIGRATIONS=1` 会自动执行 `alembic upgrade head`。

5. 启动应用并确认就绪：

   ```bash
   docker compose start api worker connector
   curl -fsS http://localhost:5173/api/ready   # {"status":"ok","database":true,"lease":true}
   ```

6. 回归检查：登录、会话列表、最近消息、渠道收发各验证一次。

## 4. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `backup dir does not exist` | 备份目录未创建或挂载失败 | `mkdir -p <dir>` 后重跑；检查挂载点 |
| `pg_dump: error: connection to server ... failed` | postgres 容器未运行或密码不符 | `docker compose ps`；核对 `.env` 中的 `STAFFDECK_PG_PASSWORD` |
| 恢复后应用 503 且 `database=false` | 恢复未完成或库名/角色不符 | 查 `/api/ready` 与 postgres 日志 |
| 恢复后 `alembic_version` 落后 | 备份早于最近一次迁移 | 用 `STAFFDECK_RUN_MIGRATIONS=1` 跑一次 api |
| 备份文件明显偏小 | `pg_dump` 中途失败被 `gzip -t` 拦下 | 不会生成正式文件；查磁盘空间与容器日志 |
