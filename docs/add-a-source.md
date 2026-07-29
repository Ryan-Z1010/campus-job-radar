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

## 国聘公开企业岗位

部分企业官网会直接链接到国聘的企业招聘专页。确认专页在未登录状态下使用
公开岗位接口，且不依赖 Cookie、验证码、私有令牌或请求签名后，可以使用
`iguopin_company`：

```json
{
  "id": "example_iguopin_company",
  "name": "示例集团",
  "type": "iguopin_company",
  "enabled": true,
  "homepage": "https://example.iguopin.com/job",
  "url": "https://gp-api.iguopin.com/api/jobs/v1/list",
  "campaign_info_url": "https://gp-api.iguopin.com/api/activity/exclusive/v1/info",
  "campaign_domain": "example",
  "target_campaign_keywords": ["2027校园招聘", "2027届校园招聘"],
  "company_id": "COMPANY_ID",
  "company": "示例集团有限公司",
  "company_type": "国企",
  "location_keywords": ["广州", "上海", "深圳", "北京"],
  "min_published_at": "2026-07-01",
  "campus_natures": ["CAMPUS_NATURE_CODE"],
  "company_name_keywords": ["示例集团"],
  "page_size": 50,
  "max_pages": 20,
  "only_applicable": true,
  "include_keywords": ["AI", "人工智能", "数据", "算法", "数字化"],
  "exclude_keywords": ["实习", "社会招聘", "销售", "2026届"],
  "graduation_years": [2027],
  "detail_url_template": "https://example.iguopin.com/job/detail?id={job_id}"
}
```

采集器先读取专页公开配置，只有招聘标题明确命中目标届别才继续；随后用
`company_id_with_sub` 覆盖集团及下属公司，按接口声明的总数分页，并校验
岗位 ID、招聘类型、公司名称和分页字段。`min_published_at` 必须按实际
秋招周期设置，不能因为旧岗位仍可申请就把它归入新一届校招。目标届别尚未
启动或岗位接口返回空数组时正常结束；分页不完整、响应结构变化或出现非
目标集团岗位时会报告来源错误。

## Hotjob 公开校招岗位

部分大易/Hotjob 招聘门户会由公开校招页面直接以表单 POST 请求分页岗位。
只有确认请求来自官网公开页面、且不依赖登录态、验证码、私有令牌或签名时，
才可使用 `hotjob_campus`：

```json
{
  "id": "example_hotjob",
  "name": "示例集团",
  "type": "hotjob_campus",
  "enabled": true,
  "homepage": "https://wecruit.hotjob.cn/TENANT/pb/school.html",
  "url": "https://wecruit.hotjob.cn/wecruit/positionInfo/listPosition/TENANT?iSaJAx=isAjax&request_locale=zh_CN",
  "tenant_id": "TENANT",
  "recruit_type": 1,
  "page_size": 15,
  "max_pages": 20,
  "company": "示例集团",
  "company_type": "国企",
  "location_keywords": ["广州", "上海", "深圳", "北京"],
  "min_published_at": "2026-07-01",
  "target_keywords": ["2027校园招聘", "2027届校园招聘", "2027秋招"],
  "include_keywords": ["AI", "人工智能", "数据", "算法", "数字化"],
  "exclude_keywords": ["实习", "社会招聘", "销售", "客服"],
  "graduation_years": [2027],
  "url_template": "https://wecruit.hotjob.cn/TENANT/pb/posDetail.html?postId={postId}&postType=campus"
}
```

采集器按 `totalPage` 逐页请求，超过 `max_pages` 会失败并提醒人工核对。正式
届别应优先依据 `projectName`，再同时使用 `publishFirstDate` 设置周期下限；
不能仅凭岗位仍在线或截止日期较晚，就把上一届岗位判断为新一届岗位。

## 广州工控官网公开岗位

广州工控集团官网的社会招聘、校园招聘和海外招聘页面共用公开岗位 API，
其中 `type=1` 为校园招聘。`giihg_campus` 会按接口声明总数分页，并校验每条
记录仍属于校园招聘：

```json
{
  "id": "guangzhou_industrial_investment_group",
  "name": "广州工控集团",
  "type": "giihg_campus",
  "enabled": true,
  "homepage": "https://www.giihg.com/xyzp",
  "url": "https://www.giihg.com/prod-api/api/recruit/list",
  "recruit_type": 1,
  "page_size": 100,
  "max_pages": 10,
  "company": "广州工业投资控股集团有限公司",
  "company_type": "国企",
  "location_keywords": ["广州", "上海", "深圳", "北京"],
  "min_published_at": "2026-07-01",
  "include_keywords": ["AI", "人工智能", "数据", "软件", "工业互联网", "数字化"],
  "exclude_keywords": ["实习", "社会招聘", "销售", "2026届"],
  "graduation_years": [2027]
}
```

当校园招聘总数为 0 时正常返回空结果；`rows` 或 `total` 消失、记录招聘类型
不符、岗位缺少稳定 ID、实际分页数量少于声明总数时都会报告来源错误。岗位
详情由官网校园招聘页弹窗展示，因此报告链接回官方校园招聘页，不构造不存在
的岗位详情地址。

## 广东国企招聘公开岗位

广东省人才市场的“广东国企招聘”页面为各省属集团设置稳定的 `gid`，并通过
`/touristApi/listJob` 提供校园招聘与社会招聘岗位。`gdrc_group` 固定目标
集团编号和校园招聘标记，按接口声明总数分页：

```json
{
  "id": "example_gdrc_group",
  "name": "示例省属集团",
  "type": "gdrc_group",
  "enabled": true,
  "homepage": "https://jq.gdrc.com/gqzp/position.html?type=school",
  "url": "https://jq.gdrc.com/touristApi/listJob",
  "group_id": "1004",
  "campus_flag": 0,
  "page_size": 50,
  "max_pages": 20,
  "company": "示例省属集团有限公司",
  "company_type": "国企",
  "location_keywords": ["广州", "上海", "深圳", "北京"],
  "min_published_at": "2026-07-01",
  "include_keywords": ["AI", "人工智能", "数据", "软件", "智能交通"],
  "exclude_keywords": ["实习", "社会招聘", "销售", "2026届"],
  "graduation_years": [2027]
}
```

接口正常返回 0 个岗位时视为空结果；集团编号不符、校园招聘标记异常、岗位
缺少稳定 ID、分页字段变化或实际返回数量少于声明总数时会报告来源错误。

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

## 北森门户公告追踪

北森招聘门户的岗位页通常由 JavaScript 动态加载，但门户首页会在公开的
`BSGlobal` 配置中列出每个页面当前的 `HtmlAddress`。不要把某一次发布产生
的 OSS 文件地址永久写死；`beisen_portal_campaign` 会先读取门户配置，再
跟随当前公告页检查目标届别：

```json
{
  "id": "example_beisen_campaign",
  "name": "示例公司",
  "type": "beisen_portal_campaign",
  "enabled": true,
  "homepage": "https://example.zhiye.com/",
  "tenant_name": "example",
  "page_names": ["招聘公告", "校招公告"],
  "target_keywords": ["2027校园招聘", "2027届校园招聘", "2027秋招"],
  "exclude_keywords": ["拟录用", "录用结果"],
  "external_id": "example-campus-2027-launch",
  "title": "示例公司2027校园招聘已启动",
  "campus_jobs_url": "https://example.zhiye.com/campus/jobs",
  "graduation_years": [2027]
}
```

适配器会校验租户名称和 `Pages` 结构。目标页面尚未出现 2027 标识时正常
返回空结果；门户配置消失、租户不符或公告页面无法定位时则报告来源错误。

## 北森旧版服务端校园岗位

部分北森门户仍使用服务端渲染的岗位表格，列表页直接提供岗位 ID、职位、
招聘单位、地点、发布时间和 `PageIndex` 分页。确认详情页的招聘类别确实是
校园招聘后，可以使用 `beisen_legacy_campus`：

```json
{
  "id": "example_beisen_legacy",
  "name": "示例集团",
  "type": "beisen_legacy_campus",
  "enabled": true,
  "homepage": "https://example.zhiye.com/xzzw",
  "page_url_template": "https://example.zhiye.com/xzzw/?PageIndex={page}",
  "required_text": "职位名称",
  "max_pages": 20,
  "company_type": "国企",
  "location_keywords": ["广州", "上海", "深圳", "北京"],
  "min_published_at": "2026-07-01",
  "include_keywords": ["AI", "数据", "软件", "数字化", "信息化"],
  "exclude_keywords": ["实习", "社招", "销售", "2026届"],
  "graduation_years": [2027]
}
```

该适配器只读取公开列表，不登录或自动投递。页面标识或必要字段消失、分页
超过上限时会报告来源错误；列表正常但没有符合日期、地点和关键词的岗位时
返回空结果。

## 猎聘专题静态校园岗位

部分企业从官网直接链接到猎聘定制专题，专题页面再读取公开静态 JSON 渲染
岗位。只有当岗位详情还能明确验证当前招聘届别时，才可使用
`liepin_static_campus`，不能仅凭静态文件最近修改时间推断届别：

```json
{
  "id": "example_liepin_campus",
  "name": "示例集团猎聘校招",
  "type": "liepin_static_campus",
  "enabled": true,
  "homepage": "https://xy.liepin.com/example/job.html",
  "url": "https://xy.liepin.com/example/js/job.json",
  "required_text": "招聘岗位",
  "max_items": 500,
  "campaign_probe_domains": ["duomian.com"],
  "max_campaign_probes": 5,
  "company_type": "国企",
  "location_keywords": ["广州", "上海", "深圳", "北京"],
  "include_keywords": ["AI", "数据", "软件", "数字化"],
  "exclude_keywords": ["实习", "社招", "销售"],
  "target_campaign_keywords": ["示例集团2027届校园招聘"],
  "previous_campaign_keywords": ["示例集团2026届校园招聘"],
  "graduation_years": [2027]
}
```

适配器先读取专题和静态岗位数组，再按地点及方向筛出候选岗位，并优先访问
公开的多面岗位详情确认项目届别。详情仍属于上一届时正常返回空结果；既
无法确认目标届别、也没有明确上一届标识时报告来源错误，避免静默误报。

## 接入完成检查

每接入一家公司后，必须在同一次任务中完成以下收尾工作：

1. 为新增配置或采集器补充测试，并运行完整测试集；
2. 对公开来源执行一次实时检查，确认“有岗位”和“暂无目标届别岗位”都能
   被正确区分；
3. 更新 README 中的来源或采集器说明；
4. 同步更新本地秋招追踪表“目标公司池”的“监控状态”：
   - 公司已有独立来源时标记为“已接入”；
   - 仅由集团级来源覆盖时标记为“集团来源已覆盖”；
   - 不得把集团级监控写成子公司已有独立采集器。

本地追踪表属于个人求职数据，不提交到仓库；如果当前环境没有该文件，应在
交付说明中明确指出尚未同步，不能静默跳过。
