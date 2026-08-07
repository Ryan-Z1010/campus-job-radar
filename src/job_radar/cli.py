from __future__ import annotations

import argparse
import json
import sys

from .agents import OrchestratorAgent, write_agent_trace
from .audit import audit_sources
from .config import ConfigError, load_dotenv, load_profile, load_sources
from .notifications import send_email
from .pipeline import run_pipeline


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
