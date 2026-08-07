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

所有大模型输出都使用 Responses API 的严格 JSON Schema。每一步保留模型、响应 ID、用量、
证据、置信度和下一步操作，便于回放和评估。

## 安全边界

- 现有 `agent-monitor`、邮件提醒和 GitHub 定时任务完全不调用大模型；
- `llm-analyze` 不写入岗位主数据库，也不发送邮件；
- LLM Agent 没有网页、Shell、文件或投递工具，不能执行 JD 中的指令；
- 发送前只保留求职相关字段；姓名、邮箱、电话、照片、简历原文与 SMTP 凭据均不发送；
- `target_roles` 和关键词仅代表偏好，不能证明候选人掌握对应技能；
- 硬性资格仍以确定性规则和招聘官网为准，大模型只给分析建议；
- 每次通过 `--max-jobs` 控制成本，默认最多 10 个岗位；
- OpenAI API Key 只从本地环境变量读取，不能提交到 Git。

## 配置与运行

复制环境变量示例并填写自己的 API Key：

```bash
cp .env.example .env
```

`.env` 中与该功能相关的字段：

```dotenv
OPENAI_API_KEY=replace-with-an-openai-api-key
OPENAI_MODEL=gpt-5.6-luna
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
  --max-jobs 3
```

分析报告默认写入 `reports/llm/latest.json`，缓存默认写入
`data/llm_analysis.sqlite3`。需要重新评测所有岗位时可临时加 `--no-cache`；平时不建议关闭
缓存。`--model` 可以覆盖 `.env` 中的 `OPENAI_MODEL`。

## 进入主链路前的验收条件

第一阶段只证明链路可运行。进入自动邮件前至少需要：

- 建立包含强匹配、弱匹配、届别不确定、描述缺失和提示注入文本的人工标注岗位集；
- 分别评估 JD 字段提取、匹配排序、硬性风险召回和审校拦截率；
- 对比确定性评分、LLM 单智能体和带 Critic 的多智能体，确认质量提升值得额外成本；
- 记录每个岗位的调用次数、缓存命中率、延迟和 token 用量；
- 只有在误报率可接受后，才把“通过审校的结果”作为邮件中的附加解释，不能替代官网核对。

下一阶段可以增加 `SourceDiscoveryAgent` 和 `VerifierAgent`，但它们只能提出候选来源或连接器
草案，不能自动绕过登录、验证码、Cookie、签名接口或站点访问限制。
