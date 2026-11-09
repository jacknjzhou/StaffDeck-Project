#!/usr/bin/env bash
# StaffDeck PostgreSQL 逻辑备份。
#
# 通过 compose 在 postgres 容器内执行 pg_dump，再在本机 gzip 落盘。备份目录必须
# 预先存在（脚本不替你创建），避免磁盘挂载没生效时把备份悄悄写到容器层或根分区。
#
# 用法：
#   bash scripts/backup_db.sh
#   STAFFDECK_BACKUP_DIR=/mnt/backups bash scripts/backup_db.sh
#
# 环境变量：
#   STAFFDECK_BACKUP_DIR            备份目录，默认 <repo>/backups
#   STAFFDECK_BACKUP_RETENTION_DAYS 保留天数，默认 14
#
# 定时任务示例见 docs/backup-restore.md。
set -euo pipefail

# 备份内含全部业务数据：新建文件默认仅属主可读，避免落在共享目录时被其他用户读走。
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/packaging/docker/docker-compose.yml"
BACKUP_DIR="${STAFFDECK_BACKUP_DIR:-${REPO_ROOT}/backups}"
RETENTION_DAYS="${STAFFDECK_BACKUP_RETENTION_DAYS:-14}"

if [[ ! -d "${BACKUP_DIR}" ]]; then
  printf 'backup dir does not exist: %s\n' "${BACKUP_DIR}" >&2
  printf 'create it first: mkdir -p %s\n' "${BACKUP_DIR}" >&2
  exit 1
fi

# 上次被 kill -9 或断电留下的半成品：超过 1 小时仍存在就不是并发备份，删掉即可。
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'staffdeck-*.sql.gz.partial' \
  -mmin +60 -delete || printf 'warning: failed to sweep stale partial backups\n' >&2

target="${BACKUP_DIR}/staffdeck-$(date +%F-%H%M%S).sql.gz"
# 先写临时文件再改名：pg_dump 中途失败时不会留下一个看起来完整的 .sql.gz。
tmp="${target}.partial"
trap 'rm -f "${tmp}"' EXIT
# 收到 INT/TERM 时先退出，再由 EXIT trap 清掉半成品；退出码保留“被信号终止”的语义。
trap 'exit 130' INT
trap 'exit 143' TERM

docker compose -f "${COMPOSE_FILE}" exec -T postgres \
  pg_dump -U staffdeck staffdeck | gzip > "${tmp}"

# 截断的 gzip 说明管道中途失败（pipefail 已覆盖 docker 侧，这里兜住磁盘写满等本机故障）。
gzip -t "${tmp}"
[[ -s "${tmp}" ]] || {
  printf 'backup is empty: %s\n' "${tmp}" >&2
  exit 1
}

mv "${tmp}" "${target}"
chmod 600 "${target}"
trap - EXIT

# 保留最近 N 天的备份；find 的 -mtime 以 24 小时为单位向上取整，14 即约两周。
# 清理失败（只读挂载、权限不足）不能影响本次备份的成功返回。
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'staffdeck-*.sql.gz' \
  -mtime "+${RETENTION_DAYS}" -delete || printf 'warning: retention cleanup failed\n' >&2

printf '%s\n' "${target}"
