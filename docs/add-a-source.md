# 新增招聘来源

## 先判断是否适合自动采集

优先级从高到低：

1. 招聘单位公开 JSON API；
2. RSS/Atom；
3. 服务端渲染的稳定 HTML；
4. 必须执行 JavaScript 的页面；
5. 需要登录、验证码或私有 Token 的页面。

第 5 类默认不接入。不要通过模拟登录、破解签名或规避验证码来采集。

## JSON API

在 `configs/sources.json` 增加：

```json
{
  "id": "example",
  "name": "示例公司",
  "type": "json_api",
  "enabled": true,
  "url": "https://careers.example.com/api/jobs",
  "list_path": "data.items",
  "company": "示例公司",
  "company_type": "国企",
  "field_map": {
    "external_id": "id",
    "title": "name",
    "location": "city",
    "description": "description",
    "published_at": "publishedAt",
    "deadline": "deadline",
    "url": "applyUrl"
  }
}
```

`list_path` 和字段映射支持以点分隔的对象路径。

公开 API 如果使用 POST，可以增加请求体和详情链接模板：

```json
{
  "type": "json_api",
  "url": "https://careers.example.com/api/jobs",
  "method": "POST",
  "request_json": {
    "recruitmentType": "campus",
    "pageNum": 1,
    "pageSize": 100
  },
  "list_path": "data.list",
  "field_map": {
    "external_id": "id",
    "title": "name",
    "company": "organization",
    "location": "city"
  },
  "url_template": "https://careers.example.com/job?id={id}"
}
```

请求体必须只使用官网前端公开发送的参数，不要复制登录 Cookie、私有
Token 或用于绕过访问控制的字段。

## 普通 HTML

`html_links` 会读取页面中的 `<a>`，按标题关键词筛选。它适合公告列表或服务端渲染的岗位列表，不适合只返回空壳 HTML 的 SPA。

```json
{
  "id": "example_html",
  "name": "示例公司",
  "type": "html_links",
  "enabled": true,
  "homepage": "https://careers.example.com/jobs",
  "company": "示例公司",
  "company_type": "外企",
  "location": "广州",
  "include_keywords": ["数据", "算法", "人工智能"],
  "exclude_keywords": ["社会招聘"]
}
```

对于复杂官网，应新增专用 Collector，并用脱敏响应 fixture 测试字段变化。

## 公开匿名会话 API

有些官网会先为所有访客签发短期匿名令牌，再调用公开岗位接口。只有在
官网前端明确为未登录访客执行该流程、且不需要账号、Cookie、验证码或
私有凭据时，才可实现专用采集器。

南方电网适配器遵循官网前端的 `guestLogin` 流程：每次运行新建匿名会话，
随后请求 `/webPost/search`。令牌只保存在当前进程内存中，不写入配置、
fixture、日志、报告或数据库。测试 fixture 中只能使用虚构令牌与脱敏岗位。
