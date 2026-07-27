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

如果公开页面背后的岗位请求必须附带前端签名、CSRF 或验证码，不要复制
其签名算法来伪装浏览器。可先用 `web_notice` 接入官网已经公开的招聘活动
和投递入口，确保用户不会错过启动时间；站点以后提供稳定公开接口时再接
岗位明细。

## 公开公告 JSON

有些官网使用公开 JSON 渲染公告列表，但没有开放稳定的岗位明细接口。
`notice_json` 可以只筛选目标届别的正式校招公告，并排除实习、社招和录用
结果。当前字段约定适用于标题为 `text3`、招聘单位为 `text1`、发布时间为
`text4`、截止时间为 `text5` 的公告列表：

```json
{
  "id": "example_notice",
  "name": "示例公司",
  "type": "notice_json",
  "enabled": true,
  "homepage": "https://careers.example.com/",
  "url": "https://careers.example.com/notices.json",
  "list_path": "data.list",
  "company": "示例公司",
  "company_prefix": "示例公司",
  "company_type": "央企",
  "target_keywords": ["2027校园招聘", "2027届校招"],
  "exclude_keywords": ["实习", "社会招聘", "拟录用", "录用结果"],
  "graduation_years": [2027]
}
```

公告标题中的简单 HTML（例如 `<br>`）会转换为普通文本。匹配不到目标届别
时返回空结果；`list_path` 不存在或不指向数组时会报告结构错误。

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

## 智联校园公开公司页

部分智联校园公司页会在服务端 HTML 的 `window.__INITIAL_DATA__` 中公开完整
岗位列表，无需登录、Cookie、验证码或请求签名。可以使用
`zhaopin_campus_company`，同时配置目标公司编号、届别起始日期和岗位
关键词：

```json
{
  "id": "example_zhaopin_company",
  "name": "示例公司",
  "type": "zhaopin_campus_company",
  "enabled": true,
  "homepage": "https://xiaoyuan.zhaopin.com/company/COMPANY_NUMBER",
  "company_number": "COMPANY_NUMBER",
  "company": "示例公司",
  "company_type": "央企",
  "min_first_published_at": "2026-07-01",
  "work_types": ["校园"],
  "include_keywords": ["AI", "人工智能", "数据", "算法", "开发"],
  "exclude_keywords": ["销售", "客服"],
  "graduation_years": [2027]
}
```

届别判断必须优先使用 `firstPublishTime`。不要使用岗位最近更新时间，否则
上一招聘周期重新发布的岗位可能被误判为新一届秋招。公司编号不符、页面
结构变化或公开 HTML 只包含部分岗位时，采集器会报告来源错误，不会静默
产生“没有新岗位”的假象。

## 公开匿名会话 API

有些官网会先为所有访客签发短期匿名令牌，再调用公开岗位接口。只有在
官网前端明确为未登录访客执行该流程、且不需要账号、Cookie、验证码或
私有凭据时，才可实现专用采集器。

南方电网适配器遵循官网前端的 `guestLogin` 流程：每次运行新建匿名会话，
随后请求 `/webPost/search`。令牌只保存在当前进程内存中，不写入配置、
fixture、日志、报告或数据库。测试 fixture 中只能使用虚构令牌与脱敏岗位。

## 招聘启动监控

当下一届校招专题尚未发布时，不要猜测专题域名，也不要把上一届页面当成
当前岗位源。`campaign_watch` 会检查招聘单位官网是否出现目标届别；未出现
时正常返回空结果，出现后生成一次启动提醒，并优先采用官网页面中匹配到的
正式链接。

```json
{
  "id": "example_campaign",
  "name": "示例公司",
  "type": "campaign_watch",
  "enabled": true,
  "homepage": "https://example.com/talent",
  "required_text": "招聘信息",
  "target_keywords": ["2027校园招聘", "2027届校招"],
  "link_keywords": ["2027"],
  "external_id": "example-campus-2027-launch",
  "title": "示例公司2027校园招聘已启动",
  "graduation_years": [2027]
}
```
