# 贡献指南

感谢你帮助完善 CampusJobRadar。

## 开发

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m job_radar run --dry-run --include-demo
```

新增来源时，请：

1. 只使用无需登录即可访问的公开信息；
2. 优先寻找招聘单位公开的 JSON、RSS 或稳定 HTML 页面；
3. 使用最小请求频率，并设置超时；
4. 用脱敏 fixture 编写测试，不提交真实求职者数据；
5. 在 `docs/source-audit.md` 记录入口、字段、限制和核验日期。

不要提交邮箱、SMTP 密码、Cookie、Token、简历、照片或完整的本地岗位数据库。
