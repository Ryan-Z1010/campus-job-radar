# 大模型多智能体分析

这是 CampusJobRadar 的可选语义分析支线。第一阶段的目标不是让大模型接管招聘监控，
而是让它在确定性采集、清洗和硬性规则之后，处理规则难以可靠判断的 JD 语义。

## 当前实现

1. `JDUnderstandingAgent`：从岗位标题、描述、学历、届别和地点中提取结构化要求；没有原文
   证据时必须留空。
2. `SemanticMatchingAgent`：将岗位要求与脱敏求职画像匹配，输出 0—100 分、匹配证据、
   能力缺口和硬性条件风险。
3. `CriticAgent`：独立检查匹配结果是否把求职偏好误当已掌握技能、是否引用不存在的经历、
   是否漏掉届别/学历/地点风险，以及分数是否过高。
4. `LlmRecruitmentOrchestrator`：审校要求修改时只重做一次匹配和审校；仍未通过就标记
   `needs_review`，不会继续循环调用。
5. `LlmAnalysisCache`：使用岗位指纹、岗位内容哈希、脱敏画像哈希、模型和三份提示词版本
   作为 SQLite 缓存键，只对新增或内容发生变化的岗位重新调用模型。

所有大模型输出都通过火山方舟 Chat API 请求 JSON，并在 Agent 层按 Schema、证据和置信度再次校验。每一步保留模型、响应 ID、用量、证据、置信度和下一步操作，便于回放和评估。
分析结果还会计算一个只读的可投递门槛：央企/国企必须硬性资格为“符合”、岗位方向被 LLM 确认适配且 Critic 判定 accept；外企/私企在此基础上还需达到默认 70 分。城市不参与加分；不满足推荐门槛时不会进入岗位推荐邮件，但会按情况进入独立的人工复核邮件。画像中的 `accepted_recruitment_windows` 可明确配置可参加的招聘季；当前示例为 `2026秋招` 和 `2027春招`，季节缺失或超出窗口的岗位仍需人工核对。

## 安全边界

- 现有 `agent-monitor` 和确定性回退链路不调用大模型；`llm-gated-monitor` 只在独立工作流中调用大模型；
- `llm-analyze` 不写入岗位主数据库，默认不发送邮件；只有显式 `--send-email` 才会调用 SMTP；
- LLM Agent 没有网页、Shell、文件或投递工具，不能执行 JD 中的指令；
- 发送前只保留求职相关字段；姓名、邮箱、电话、照片、简历原文与 SMTP 凭据均不发送；
- `target_roles` 和关键词仅代表偏好，不能证明候选人掌握对应技能；
- 硬性资格仍以确定性规则和招聘官网为准；大模型只能依据岗位原文证据解析“待核对/需核对”，不能凭空覆盖硬性条件；
- 每次通过 `--max-jobs` 控制成本，默认最多 50 个岗位，并优先选择确定性资格为“待核对/需核对”的岗位；分析结果会进入缓存，后续运行继续处理尚未分析的岗位；
- 豆包连接级错误最多自动重试一次；认证、权限和参数类 HTTP 错误不会重试；
- 火山方舟 API Key 只从本地环境变量或用户指定的本地 Key 文件读取，不能提交到 Git。

## 配置与运行

复制环境变量示例并填写自己的火山方舟 API Key：

```bash
cp .env.example .env
```

`.env` 中与该功能相关的字段：

```dotenv
ARK_API_KEY=replace-with-an-ark-api-key
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_MODEL=doubao-seed-2-0-lite-260428
```

也可以不复制 Key 到 .env，直接将本地交接文件传给 CLI；程序只读取其中的 Key 值，不会打印、复制或写入仓库。示例：

```bash
python -m job_radar llm-analyze \
  --key-file "/path/to/实习生api_key.txt" \
  --profile configs/profile.local.json \
  --include-demo \
  --source demo_official_jobs \
  --max-jobs 1
```

推荐先复制个人画像，再加入有事实依据、且不包含联系方式的数组：

```json
{
  "skills": ["Python", "SQL"],
  "experience_highlights": ["一句话说明自己实际完成的工作"],
  "project_highlights": ["一句话说明项目问题、方法和结果"],
  "language_qualifications": ["已取得的语言成绩"]
}
```

不要把“想做大模型岗位”写进 `skills`，除非有课程、实习或项目证据。偏好继续放在
`target_roles` 和 `positive_keywords`。

从演示岗位开始：

```bash
python -m job_radar llm-analyze \
  --profile configs/profile.local.json \
  --include-demo \
  --source demo_official_jobs \
  --max-jobs 3 \
  --notification-preview-dir reports/llm/notification-preview
```

分析报告默认写入 `reports/llm/latest.json`，缓存默认写入
`data/llm_analysis.sqlite3`。需要重新评测所有岗位时可临时加 `--no-cache`；平时不建议关闭
缓存。`--notification-preview-dir` 会生成通过门槛岗位的 HTML/JSON/CSV 预览，并在其 `manual-review/` 子目录生成需要人工复核的岗位、原因和官方链接；预览本身不发送邮件。
只有同时显式传入 `--send-email` 和预览目录，程序才会把通过门槛岗位交给 SMTP；没有通过岗位时不会发送。若 LLM 对“待核对/需核对”岗位仍返回 `needs_review` 或分析失败，同一次运行会额外发送一封独立的“待人工复核”邮件；确定性队列不会在未经 LLM 分析时直接发给你，这封邮件明确说明不代表推荐直接投递，并附官方岗位链接供人工核对。
推荐通知使用 `data/llm_notification.sqlite3`，人工复核通知使用独立的 `data/llm_review_notification_v2.sqlite3`，两者分别按岗位指纹去重；SMTP 失败不会记录，后续运行会重试。
`--model` 可以覆盖 .env 中的 ARK_MODEL；如果使用推理接入点，也可以填写 ep-...。默认模型为 doubao-seed-2-0-lite-260428。

## 进入主链路前的验收条件

第一阶段只证明链路可运行。进入自动邮件前至少需要：

- 建立包含强匹配、弱匹配、届别不确定、描述缺失和提示注入文本的人工标注岗位集；
- 分别评估 JD 字段提取、匹配排序、硬性风险召回和审校拦截率；
- 对比确定性评分、LLM 单智能体和带 Critic 的多智能体，确认质量提升值得额外成本；
- 记录每个岗位的调用次数、缓存命中率、延迟和 token 用量；
- 只有在误报率可接受后，才把“通过审校的结果”作为邮件中的附加解释，不能替代官网核对。

下一阶段可以增加 `SourceDiscoveryAgent` 和 `VerifierAgent`，但它们只能提出候选来源或连接器
草案，不能自动绕过登录、验证码、Cookie、签名接口或站点访问限制。
