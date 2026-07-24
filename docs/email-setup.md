# QQ 邮箱提醒配置

本地 `.env` 已经填写收件地址、SMTP 服务器和端口，只缺
`SMTP_PASSWORD`。

## 你需要完成

1. 登录网页版 QQ 邮箱。
2. 进入邮箱设置中的账号与安全/客户端服务区域。
3. 开启 POP3/SMTP 或 IMAP/SMTP 服务。
4. 按页面要求完成安全验证并生成“授权码”。
5. 在你自己的电脑上打开项目根目录的 `.env`。
6. 将授权码填写在 `SMTP_PASSWORD=` 后面。

这里填写的是授权码，不是 QQ 登录密码。不要把授权码发到聊天、Issue、
README 或公开仓库。

## 测试

先执行：

```bash
cd /Users/ryan/Documents/1
python3 -m job_radar test-email
```

看到“测试邮件已发送”后，检查收件箱和垃圾邮件目录。之后真实监控可以运行：

```bash
python3 -m job_radar run \
  --profile configs/profile.local.json \
  --sources configs/sources.json
```

只有首次出现且达到评分阈值的岗位才会发信。
