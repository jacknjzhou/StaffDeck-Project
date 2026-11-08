# Application update reminder（已废弃）

> **状态：已废弃（2026-11-08）。** StaffDeck 已改为服务端优先交付，桌面安装包不再是
> 支持的发行渠道，客户端内的“有新版本”提醒也随之退役。本文件仅保留给仍在运行旧
> 桌面版本的环境作历史参考。

## 为什么废弃

- 交付形态改为 Docker Compose 服务端部署，用户通过浏览器访问，不存在需要逐台升级的
  桌面客户端。
- 版本推进由服务端镜像完成：升级 = 替换镜像 + 执行迁移，不依赖客户端轮询 GitHub
  Releases，也不依赖用户点击提醒。
- 继续保留客户端更新检查会与“服务端是唯一版本源”冲突，并产生无意义的外网请求。

## 旧版行为（仅供排查）

旧桌面构建在登录并完成首次引导后，best-effort 请求 `GET /api/app/version`，从
`https://github.com/OpenBMB/StaffDeck/releases.atom` 读取最新兼容版本，成功结果缓存
六小时、失败缓存十五分钟；只弹 30 秒提示，不下载、不安装。提示去重键为
`staffdeck_update_reminded_version`。`STAFFDECK_UPDATE_CHECK` 可显式开关。以上逻辑
不再演进，也不再接受兼容性修复。

## 现在如何升级

1. 备份 PostgreSQL（`pg_dump`）并确认 `STAFFDECK_APP_SECRET` 已单独保管。
2. 拉取新镜像并重建：`cd packaging/docker && docker compose pull && docker compose up -d`。
3. `api` 容器会在启动前执行 `python -m app.db.migrations_cli upgrade`；`worker` /
   `connector` 的 `STAFFDECK_RUN_MIGRATIONS` 保持为 `0`。
4. 验证 `/api/health` 与关键页面，再恢复 `worker` / `connector` 流量。

完整步骤与回滚注意事项见 `docs/server-deployment.md`。
