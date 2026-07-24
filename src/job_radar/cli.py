from __future__ import annotations

import argparse
import json
import sys

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
