# GitHub 部署指南

## 1. 先创建仓库

建议先设为 Private，确认没有个人数据后再改为 Public。不要上传简历、照片、真实邮箱、本地 `.env`、`profile.local.json` 或数据库。

## 2. 配置 Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

- `ALERT_EMAIL`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_USE_SSL`
- `ARK_API_KEY`
- `ARK_BASE_URL`（可选，默认火山方舟北京 Base URL）
- `ARK_MODEL`（可选，默认 `doubao-seed-2-0-lite-260428`）
- `LLM_PROFILE_JSON`（推荐；脱敏后的画像 JSON，不包含姓名、邮箱、电话或简历原文）

邮箱应使用应用专用密码。LLM 工作流只有在 Ark API、脱敏画像和以上邮件配置均完整时才发送，否则只生成预览。

## 3. 开启真实来源

专用适配器完成并通过测试后，将对应来源的 `enabled` 改为 `true`。演示来源即使保持启用，也会因为带有 `demo: true` 而被定时任务跳过。

## 4. 数据持久化

工作流用 GitHub Actions cache 保存 `data/llm_analysis.sqlite3` 分析缓存和 `data/llm_notification.sqlite3` 已成功发送岗位指纹，并上传当次岗位报告和通知预览 artifact。通知指纹只有在 SMTP 发送成功后才写入；Cache 可能被清理，因此它适合作为 MVP 去重状态，不应视为永久数据库。后续公开服务建议迁移到托管数据库。

## 5. 调度注意事项

`LLM gated job monitor` 工作流负责定时执行 LLM 门槛分析：默认最多分析 3 个岗位，并以 4 个并行采集 worker 获取来源，生成报告和通知预览；只有定时运行或手动显式勾选 `send_email`，且 SMTP Secrets 完整时，才会发送通过门槛的岗位。`LLM_PROFILE_JSON` 存在时只在 Runner 临时目录使用，不会上传到 Artifact。
`reports/llm/latest.json` 同时记录确定性采集的来源统计和来源错误，便于区分“当前没有岗位”和“来源暂时不可访问”。

首次验证或排查来源时，建议手动运行并填写 `source_id=demo_official_jobs`、`include_demo=true`、`max_jobs=1`、`send_email=false`，这样只验证一条演示岗位的 LLM 链路，不必等待全部招聘来源采集。正式定时运行不填写 `source_id`，仍会扫描所有启用的真实来源。

`Daily job monitor` 保留为手动确定性 Agent/legacy 回退入口，避免与 LLM 工作流重复发邮件。手动运行默认不发邮件，只有显式勾选 `send_email` 且 Secrets 完整时才会发送。

GitHub 的定时任务使用 UTC，可能在高负载时延迟，且只运行默认分支上的工作流。公开仓库长期无活动时，GitHub 也可能停用 scheduled workflow。重要截止日期仍应回到招聘官网核对。
