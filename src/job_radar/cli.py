from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .agents import OrchestratorAgent, write_agent_trace
from .audit import audit_sources
from .config import ConfigError, load_dotenv, load_profile, load_sources
from .notifications import send_email
from .pipeline import run_pipeline


def _read_doubao_key_file(path: str) -> str:
    """Read only the key value from a local Ark handoff file."""

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError("豆包 Key 文件无法读取: {}".format(exc)) from exc
    for line in text.splitlines():
        match = re.match(r"^\s*Key\s*值\s*[:：]\s*(\S+)\s*$", line)
        if match:
            return match.group(1).strip()
    raise ConfigError("豆包 Key 文件中没有找到“Key 值”字段")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-radar",
        description="采集、去重并按个人偏好排序公开招聘信息。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="执行一次完整监控")
    run.add_argument("--profile", default="configs/profile.example.json")
    run.add_argument("--sources", default="configs/sources.json")
    run.add_argument("--database", default="data/job_radar.db")
    run.add_argument("--report-dir", default="reports/latest")
    run.add_argument("--env-file", default=".env")
    run.add_argument("--dry-run", action="store_true", help="生成报告但不发送邮件")
    run.add_argument("--include-demo", action="store_true", help="显式启用演示岗位")

    agent_run = subparsers.add_parser(
        "agent-run",
        help="以多 Agent 影子模式运行，不入库也不发送邮件",
    )
    agent_run.add_argument("--profile", default="configs/profile.example.json")
    agent_run.add_argument("--sources", default="configs/sources.json")
    agent_run.add_argument(
        "--trace-file",
        default="reports/agents/latest.json",
        help="保存 Agent 决策轨迹的 JSON 文件",
    )
    agent_run.add_argument(
        "--source",
        action="append",
        help="只运行指定来源 ID，可重复传入",
    )
    agent_run.add_argument("--include-demo", action="store_true", help="显式启用演示岗位")

    agent_monitor = subparsers.add_parser(
        "agent-monitor",
        help="以多 Agent 正式模式采集、入库、生成报告并按需发邮件",
    )
    agent_monitor.add_argument("--profile", default="configs/profile.example.json")
    agent_monitor.add_argument("--sources", default="configs/sources.json")
    agent_monitor.add_argument("--database", default="data/job_radar.db")
    agent_monitor.add_argument("--report-dir", default="reports/latest")
    agent_monitor.add_argument(
        "--trace-file",
        default="reports/agents/latest.json",
        help="保存完整 Agent 决策轨迹",
    )
    agent_monitor.add_argument("--env-file", default=".env")
    agent_monitor.add_argument(
        "--source",
        action="append",
        help="只运行指定来源 ID，可重复传入",
    )
    agent_monitor.add_argument(
        "--dry-run",
        action="store_true",
        help="正常入库并生成报告，但不发送邮件",
    )
    agent_monitor.add_argument(
        "--include-demo", action="store_true", help="显式启用演示岗位"
    )

    llm_analyze = subparsers.add_parser(
        "llm-analyze",
        help="用可选的大模型智能体分析岗位，不入主库也不发送邮件",
    )
    llm_analyze.add_argument("--profile", default="configs/profile.example.json")
    llm_analyze.add_argument("--sources", default="configs/sources.json")
    llm_analyze.add_argument("--env-file", default=".env")
    llm_analyze.add_argument(
        "--key-file",
        help="本地火山方舟 Key 交接文件；只在内存中读取，不会复制到仓库",
    )
    llm_analyze.add_argument(
        "--base-url",
        help="火山方舟 Base URL；默认读取 ARK_BASE_URL/DOUBAO_BASE_URL",
    )
    llm_analyze.add_argument(
        "--output",
        default="reports/llm/latest.json",
        help="保存大模型分析与审校轨迹",
    )
    llm_analyze.add_argument(
        "--notification-preview-dir",
        help="只写入通过 LLM 门槛的邮件预览目录，不发送邮件",
    )
    llm_analyze.add_argument(
        "--send-email",
        action="store_true",
        help="显式发送通过 LLM 门槛的岗位；必须同时指定 --notification-preview-dir",
    )
    llm_analyze.add_argument(
        "--cache-database",
        default="data/llm_analysis.sqlite3",
        help="按岗位内容、脱敏画像、模型和提示词版本缓存结果",
    )
    llm_analyze.add_argument(
        "--notification-database",
        default="data/llm_notification.sqlite3",
        help="记录已成功发送的 LLM 岗位指纹；只在 --send-email 时写入",
    )
    llm_analyze.add_argument(
        "--no-cache",
        action="store_true",
        help="跳过读取和写入大模型分析缓存",
    )
    llm_analyze.add_argument(
        "--source",
        action="append",
        help="只运行指定来源 ID，可重复传入",
    )
    llm_analyze.add_argument(
        "--max-jobs",
        type=int,
        default=10,
        help="本次最多分析的岗位数，默认 10",
    )
    llm_analyze.add_argument(
        "--notify-min-score",
        type=int,
        default=70,
        help="进入可投递门槛的最低 LLM 分数，默认 70",
    )
    llm_analyze.add_argument(
        "--collection-workers",
        type=int,
        default=4,
        help="确定性来源并行采集数；默认 4，设为 1 可恢复串行",
    )
    llm_analyze.add_argument(
        "--model",
        help="豆包模型或推理接入点；默认读取 ARK_MODEL/DOUBAO_MODEL",
    )
    llm_analyze.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="单次大模型请求超时秒数",
    )
    llm_analyze.add_argument(
        "--include-demo", action="store_true", help="显式启用演示岗位"
    )

    audit = subparsers.add_parser("audit", help="检查招聘来源是否可访问")
    audit.add_argument("--sources", default="configs/sources.json")
    audit.add_argument("--timeout", type=int, default=15)

    email = subparsers.add_parser("test-email", help="发送一封配置测试邮件")
    email.add_argument("--env-file", default=".env")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "audit":
            sources = load_sources(args.sources)
            print(json.dumps(audit_sources(sources, args.timeout), ensure_ascii=False, indent=2))
            return 0
        if args.command == "test-email":
            load_dotenv(args.env_file)
            send_email(
                "CampusJobRadar 邮件配置测试",
                (
                    "<h1>CampusJobRadar 邮件配置成功</h1>"
                    "<p>以后发现达到阈值的新岗位时，会发送到这个邮箱。</p>"
                ),
            )
            print("测试邮件已发送。")
            return 0
        if args.command == "llm-analyze":
            from .llm import (
                DoubaoChatClient,
                LlmAnalysisCache,
                LlmRecruitmentOrchestrator,
                send_llm_notification_email,
                write_llm_notification_preview,
                write_llm_report,
            )

            if args.send_email and not args.notification_preview_dir:
                raise ConfigError(
                    "--send-email 必须同时指定 --notification-preview-dir"
                )
            load_dotenv(args.env_file)
            profile = load_profile(args.profile)
            deterministic = OrchestratorAgent().run(
                profile=profile,
                sources=load_sources(args.sources),
                include_demo=args.include_demo,
                source_ids=args.source,
                collection_workers=args.collection_workers,
            )
            api_key = (
                os.environ.get("ARK_API_KEY")
                or os.environ.get("DOUBAO_API_KEY")
                or ""
            )
            if not api_key and args.key_file:
                api_key = _read_doubao_key_file(args.key_file)
            if not api_key:
                raise ConfigError(
                    "ARK_API_KEY 未配置；可使用 --key-file 指向本地豆包 Key 文件"
                )
            base_url = (
                args.base_url
                or os.environ.get("ARK_BASE_URL")
                or os.environ.get("DOUBAO_BASE_URL")
                or DoubaoChatClient.default_base_url
            )
            model = (
                args.model
                or os.environ.get("ARK_MODEL")
                or os.environ.get("DOUBAO_MODEL")
                or "doubao-seed-2-0-lite-260428"
            )
            cache = (
                None
                if args.no_cache
                else LlmAnalysisCache(args.cache_database)
            )
            result = LlmRecruitmentOrchestrator(
                DoubaoChatClient(
                    api_key=api_key,
                    model=model,
                    timeout=args.timeout,
                    base_url=base_url,
                ),
                cache=cache,
            ).run(
                deterministic.jobs,
                profile,
                max_jobs=args.max_jobs,
                notify_min_score=args.notify_min_score,
            )
            result.deterministic_source_errors = list(
                deterministic.source_errors
            )
            result.deterministic_counts = {
                "source_total": deterministic.source_total,
                "completed_sources": deterministic.completed_sources,
                "failed_sources": deterministic.failed_sources,
                "collected": deterministic.collected,
                "valid": deterministic.valid,
                "reviewed": deterministic.reviewed,
                "ready": deterministic.ready,
                "review_required": deterministic.review_required,
            }
            report_path = write_llm_report(result, args.output)
            print(
                "LLM选中 {0.selected} | 完成 {0.analyzed} | "
                "缓存命中 {0.cache_hits} | 需人工复核 {0.needs_review} | "
                "失败 {0.failed} | 可提醒 {0.notify_eligible}".format(result)
            )
            print("LLM分析报告: {}".format(report_path))
            if args.notification_preview_dir:
                preview_path = write_llm_notification_preview(
                    result, args.notification_preview_dir
                )
                print(
                    "LLM通知预览: {} | 可提醒 {} 个岗位".format(
                        preview_path, result.notify_eligible
                    )
                )
            if args.send_email:
                sent_count = send_llm_notification_email(
                    result, send_email, args.notification_database
                )
                if sent_count:
                    print("LLM邮件已发送: {} 个岗位".format(sent_count))
                else:
                    print("没有通过 LLM 门槛的岗位，邮件未发送。")
            for error in deterministic.source_errors:
                print("来源错误: {}".format(error), file=sys.stderr)
            return 2 if result.failed and result.analyzed == 0 else 0
        if args.command == "agent-run":
            result = OrchestratorAgent().run(
                profile=load_profile(args.profile),
                sources=load_sources(args.sources),
                include_demo=args.include_demo,
                source_ids=args.source,
            )
            trace_path = write_agent_trace(result, args.trace_file)
            print(
                "Agent来源 {0.source_total} | 成功 {0.completed_sources} | "
                "失败 {0.failed_sources} | 采集 {0.collected} | "
                "复核后 {0.reviewed} | 可继续 {0.ready} | "
                "需人工核对 {0.review_required}".format(result)
            )
            print("决策轨迹: {}".format(trace_path))
            for error in result.source_errors:
                print("来源错误: {}".format(error), file=sys.stderr)
            return 2 if result.failed_sources and result.collected == 0 else 0
        if args.command == "agent-monitor":
            load_dotenv(args.env_file)
            result = OrchestratorAgent().run(
                profile=load_profile(args.profile),
                sources=load_sources(args.sources),
                include_demo=args.include_demo,
                source_ids=args.source,
                database=args.database,
                report_dir=args.report_dir,
                dry_run=args.dry_run,
            )
            trace_path = write_agent_trace(result, args.trace_file)
            print(
                "Agent来源 {0.source_total} | 成功 {0.completed_sources} | "
                "失败 {0.failed_sources} | 采集 {0.collected} | "
                "有效 {0.valid} | 新增 {0.inserted} | 更新 {0.updated} | "
                "达到提醒阈值 {0.alerted} | 邮件 {1}".format(
                    result, "已发送" if result.email_sent else "未发送"
                )
            )
            print("决策轨迹: {}".format(trace_path))
            for error in result.source_errors:
                print("来源错误: {}".format(error), file=sys.stderr)
            for error in result.pipeline_errors:
                print("流程错误: {}".format(error), file=sys.stderr)
            return 2 if result.pipeline_errors or (
                result.source_errors and result.collected == 0
            ) else 0

        load_dotenv(args.env_file)
        result = run_pipeline(
            profile=load_profile(args.profile),
            sources=load_sources(args.sources),
            database=args.database,
            report_dir=args.report_dir,
            dry_run=args.dry_run,
            include_demo=args.include_demo,
        )
        print(
            "采集 {0.collected} | 有效 {0.valid} | 新增 {0.inserted} | "
            "更新 {0.updated} | 达到提醒阈值 {0.alerted}".format(result)
        )
        for error in result.source_errors:
            print("来源错误: {}".format(error), file=sys.stderr)
        return 2 if result.source_errors and result.collected == 0 else 0
    except (ConfigError, RuntimeError, ValueError) as exc:
        print("错误: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
