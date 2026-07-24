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

邮箱应使用应用专用密码。工作流只有在以上邮件配置完整时才发送，否则以 dry-run 运行。

## 3. 开启真实来源

专用适配器完成并通过测试后，将对应来源的 `enabled` 改为 `true`。演示来源即使保持启用，也会因为带有 `demo: true` 而被定时任务跳过。

## 4. 数据持久化

工作流用 GitHub Actions cache 保存 SQLite 去重库，并上传当次报告 artifact。Cache 可能被清理，因此它适合作为 MVP 去重状态，不应视为永久数据库。后续公开服务建议迁移到托管数据库。

## 5. 调度注意事项

GitHub 的定时任务使用 UTC，可能在高负载时延迟，且只运行默认分支上的工作流。公开仓库长期无活动时，GitHub 也可能停用 scheduled workflow。重要截止日期仍应回到招聘官网核对。
