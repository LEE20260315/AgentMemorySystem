# 生产日志测试噪声清理记录（2026-09-30 13:01:05）

> 清理对象：`%LOCALAPPDATA%\recall-memory\logs\recall-*.log` 中由**单元测试**注入的行。
> 全量备份：`C:\Users\MR.Dong\AppData\Local\recall-memory\logs-archive\pre-cleanup-20260930-130105`
> 只删除下列列出的行，**逐行留痕**；其余一行未动。

## recall-20260928.log —— 删除 15 行

### A 类：测试签名组（同秒含 STOPPED + DEGRADED executed=1 failed=1）

- **2026-09-28 16:02:26**（5 行）
  - `2026-09-28 16:02:26 [INFO] [AUTOPILOT OK] executed=1 failed=0 skipped=0 rejected=0`
  - `2026-09-28 16:02:26 [INFO] [AUTOPILOT OK] executed=0 failed=0 skipped=1 rejected=0`
  - `2026-09-28 16:02:26 [WARNING] [AUTOPILOT STOPPED] stop.flag present`
  - `2026-09-28 16:02:26 [INFO] [AUTOPILOT DEGRADED] executed=1 failed=1 skipped=0 rejected=0`
  - `2026-09-28 16:02:26 [INFO] [AUTOPILOT DEGRADED] executed=0 failed=0 skipped=0 rejected=1`
- **2026-09-28 16:06:05**（5 行）
  - `2026-09-28 16:06:05 [INFO] [AUTOPILOT OK] executed=1 failed=0 skipped=0 rejected=0`
  - `2026-09-28 16:06:05 [INFO] [AUTOPILOT OK] executed=0 failed=0 skipped=1 rejected=0`
  - `2026-09-28 16:06:05 [WARNING] [AUTOPILOT STOPPED] stop.flag present`
  - `2026-09-28 16:06:05 [INFO] [AUTOPILOT DEGRADED] executed=1 failed=1 skipped=0 rejected=0`
  - `2026-09-28 16:06:05 [INFO] [AUTOPILOT DEGRADED] executed=0 failed=0 skipped=0 rejected=1`
- **2026-09-28 16:14:54**（5 行）
  - `2026-09-28 16:14:54 [INFO] [AUTOPILOT OK] executed=1 failed=0 skipped=0 rejected=0`
  - `2026-09-28 16:14:54 [INFO] [AUTOPILOT OK] executed=0 failed=0 skipped=1 rejected=0`
  - `2026-09-28 16:14:54 [WARNING] [AUTOPILOT STOPPED] stop.flag present`
  - `2026-09-28 16:14:54 [INFO] [AUTOPILOT DEGRADED] executed=1 failed=1 skipped=0 rejected=0`
  - `2026-09-28 16:14:54 [INFO] [AUTOPILOT DEGRADED] executed=0 failed=0 skipped=0 rejected=1`

## recall-20260929.log —— 删除 16 行

### A 类：测试签名组（同秒含 STOPPED + DEGRADED executed=1 failed=1）

- **2026-09-29 10:58:05**（5 行）
  - `2026-09-29 10:58:05 [INFO] [AUTOPILOT OK] executed=1 failed=0 skipped=0 rejected=0`
  - `2026-09-29 10:58:05 [INFO] [AUTOPILOT OK] executed=0 failed=0 skipped=1 rejected=0`
  - `2026-09-29 10:58:05 [WARNING] [AUTOPILOT STOPPED] stop.flag present`
  - `2026-09-29 10:58:05 [INFO] [AUTOPILOT DEGRADED] executed=1 failed=1 skipped=0 rejected=0`
  - `2026-09-29 10:58:05 [INFO] [AUTOPILOT DEGRADED] executed=0 failed=0 skipped=0 rejected=1`
- **2026-09-29 12:49:03**（5 行）
  - `2026-09-29 12:49:03 [INFO] [AUTOPILOT OK] executed=1 failed=0 skipped=0 rejected=0`
  - `2026-09-29 12:49:03 [INFO] [AUTOPILOT OK] executed=0 failed=0 skipped=1 rejected=0`
  - `2026-09-29 12:49:03 [WARNING] [AUTOPILOT STOPPED] stop.flag present`
  - `2026-09-29 12:49:03 [INFO] [AUTOPILOT DEGRADED] executed=1 failed=1 skipped=0 rejected=0`
  - `2026-09-29 12:49:03 [INFO] [AUTOPILOT DEGRADED] executed=0 failed=0 skipped=0 rejected=1`
- **2026-09-29 13:40:30**（5 行）
  - `2026-09-29 13:40:30 [INFO] [AUTOPILOT OK] executed=1 failed=0 skipped=0 rejected=0`
  - `2026-09-29 13:40:30 [INFO] [AUTOPILOT OK] executed=0 failed=0 skipped=1 rejected=0`
  - `2026-09-29 13:40:30 [WARNING] [AUTOPILOT STOPPED] stop.flag present`
  - `2026-09-29 13:40:30 [INFO] [AUTOPILOT DEGRADED] executed=1 failed=1 skipped=0 rejected=0`
  - `2026-09-29 13:40:30 [INFO] [AUTOPILOT DEGRADED] executed=0 failed=0 skipped=0 rejected=1`

### B 类：孤立 STOPPED 行（按**来源**判定，非签名规则）

- **2026-09-29 14:22:57** —— 来源：红绿检验的 RED 运行（当时临时禁用日志重定向）
  - `2026-09-29 14:22:57 [WARNING] [AUTOPILOT STOPPED] stop.flag present`

## 合计

- 删除 **31 行**，跨 **2 个日志文件**：`recall-20260928.log` 15 行、`recall-20260929.log` 16 行。
- 备份目录保存了清理**前**的完整副本，可随时逐字节还原。
- 明确保留的真实事件示例：`2026-09-28 16:12:10 [AUTOPILOT DEGRADED] executed=4 failed=1`
  （黄金集修复前那次**真实**的 eval 失败）。

> 清理原因：这些假事件会污染 G-B10 健康信号分级与基于 DEGRADED 的监控取证。
> 根因（测试未隔离 logger）已在 commit `8f050c6` 修复，并有红绿验证与全量套件零增量验证。
