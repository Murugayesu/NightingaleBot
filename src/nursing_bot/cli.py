"""Command-line tools:  python -m nursing_bot.cli <command> --help

migrate        apply database migrations + create the job-queue schema
ingest-dir     bulk-load a folder of documents (dedupes by content hash)
eval           run the evaluation set; non-zero exit when below thresholds (CI gate)
purge-history  delete chat history older than CHAT_HISTORY_RETENTION_DAYS
gen-secrets    print fresh random values for the secret settings
"""

import argparse
import asyncio
import fnmatch
import hashlib
import json
import secrets
import sys
import uuid
from pathlib import Path
from typing import Any

from nursing_bot.config import get_settings
from nursing_bot.observability import configure_logging


# ------------------------------------------------------------------- migrate
def cmd_migrate(args: argparse.Namespace) -> int:
    from alembic.config import Config

    from alembic import command

    print("Applying database migrations ...")
    command.upgrade(Config(args.alembic_ini), "head")

    async def apply_queue_schema() -> bool:
        from nursing_bot.storage.database import dispose_engine
        from nursing_bot.workers.tasks import ensure_queue_schema

        try:
            return await ensure_queue_schema()
        finally:
            await dispose_engine()

    created = asyncio.run(apply_queue_schema())
    print(
        "Job-queue schema created." if created else "Job-queue schema already present."
    )
    print("Done.")
    return 0


# ---------------------------------------------------------------- ingest-dir
def _skip_reason(path: Path, folder: Path, exclude: list[str]) -> str | None:
    """Why a file should not be ingested, or None to ingest it."""
    from nursing_bot.ingestion.loaders import unsupported_reason

    name = path.name
    if name.startswith("~$"):
        return "Office lock file"
    if name.startswith("."):
        return "hidden file"
    relative = path.relative_to(folder).as_posix()
    if any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(relative, p) for p in exclude):
        return "excluded"
    if unsupported_reason(name):
        return "unsupported type"
    return None


async def _ingest_dir(
    folder: Path, doc_type: str, recursive: bool, exclude: list[str]
) -> int:
    from sqlalchemy import select

    from nursing_bot.ingestion.loaders import TABULAR_SUFFIXES, file_suffix
    from nursing_bot.jobs import run_ingestion
    from nursing_bot.services import get_storage
    from nursing_bot.storage.database import dispose_engine, get_session
    from nursing_bot.storage.models import Document, DocumentStatus

    files = sorted(
        p for p in (folder.rglob("*") if recursive else folder.glob("*")) if p.is_file()
    )
    storage = get_storage()
    failures = 0
    for path in files:
        if reason := _skip_reason(path, folder, exclude):
            print(f"  skip      {path.name} ({reason})")
            continue
        data = path.read_bytes()
        content_hash = hashlib.sha256(data).hexdigest()
        async with get_session() as session:
            existing = (
                await session.execute(
                    select(Document).where(Document.content_hash == content_hash)
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status == DocumentStatus.COMPLETED:
                print(f"  exists    {path.name}")
                continue
            if existing is None:
                doc_id = uuid.uuid4()
                key = f"{doc_id}/{path.name}"
                await storage.upload(key, data)
                session.add(
                    Document(
                        id=doc_id,
                        filename=path.name,
                        doc_type=doc_type,
                        content_type="tabular"
                        if file_suffix(path.name) in TABULAR_SUFFIXES
                        else "prose",
                        s3_key=key,
                        content_hash=content_hash,
                        metadata_json={"source_path": str(path.relative_to(folder))},
                    )
                )
                await session.commit()
            else:
                doc_id = existing.id
        try:
            await run_ingestion(doc_id)
            print(f"  ingested  {path.name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  FAILED    {path.name}: {exc}")
    await dispose_engine()
    return 1 if failures else 0


def cmd_ingest_dir(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Not a directory: {folder}", file=sys.stderr)
        return 2
    return asyncio.run(_ingest_dir(folder, args.doc_type, args.recursive, args.exclude))


# ---------------------------------------------------------------------- eval
def _print_eval(
    mode: str, metrics: dict[str, Any], results: list[dict[str, Any]]
) -> None:
    print(f"\n=== {mode} evaluation: {metrics.get('cases')} cases ===")
    for key, value in metrics.items():
        if key != "cases":
            print(
                f"  {key:30} {json.dumps(value) if isinstance(value, dict) else value}"
            )
    failed = [r for r in results if not r.get("passed")]
    if failed:
        print(f"\nFailures ({len(failed)}):")
        for r in failed:
            print(f"  {r['case_id']:10} {r['detail']}")


async def _eval(args: argparse.Namespace) -> int:
    from nursing_bot.evaluation.runner import (
        create_eval_run,
        execute_eval_run,
        run_evaluation,
    )
    from nursing_bot.storage.database import dispose_engine, get_session

    if args.save:
        async with get_session() as session:
            run = await create_eval_run(session, args.mode, use_judge=args.judge)
        run = await execute_eval_run(run.id)
        if run.status != "completed":
            print(f"Eval failed: {run.error}", file=sys.stderr)
            await dispose_engine()
            return 2
        metrics, results = run.metrics_json, run.results_json
    else:
        outcome = await run_evaluation(args.mode, use_judge=args.judge)
        metrics, results = outcome.metrics, outcome.results
    await dispose_engine()

    _print_eval(args.mode, metrics, results)
    if args.json:
        Path(args.json).write_text(
            json.dumps({"metrics": metrics, "results": results}, indent=2), "utf-8"
        )

    gates = {"hit_rate": args.min_hit_rate, "pass_rate": args.min_pass_rate}
    failed_gates = [
        f"{name} {metrics.get(name)} < {minimum}"
        for name, minimum in gates.items()
        if minimum is not None and (metrics.get(name) or 0.0) < minimum
    ]
    if failed_gates:
        print("\nEVAL GATE FAILED: " + "; ".join(failed_gates), file=sys.stderr)
        return 1
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    return asyncio.run(_eval(args))


# ------------------------------------------------------------- purge-history
def cmd_purge_history(args: argparse.Namespace) -> int:
    """Delete chat history past CHAT_HISTORY_RETENTION_DAYS.

    The worker does this daily in queue mode; in inline mode (no worker) run
    this from cron, e.g. `17 3 * * * python -m nursing_bot.cli purge-history`.
    """

    async def purge() -> int:
        from nursing_bot.jobs import purge_expired_chat_history
        from nursing_bot.storage.database import dispose_engine

        try:
            return await purge_expired_chat_history()
        finally:
            await dispose_engine()

    deleted = asyncio.run(purge())
    print(f"Deleted {deleted} expired chat sessions.")
    return 0


# --------------------------------------------------------------- gen-secrets
def cmd_gen_secrets(args: argparse.Namespace) -> int:
    print(f"ADMIN_API_KEY={secrets.token_urlsafe(32)}")
    print(f"SESSION_SECRET={secrets.token_urlsafe(48)}")
    print(f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}")
    print(f"S3_SECRET_ACCESS_KEY={secrets.token_urlsafe(32)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        # psycopg (used by the Procrastinate queue) cannot run on the Proactor loop.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    parser = argparse.ArgumentParser(
        prog="nursing_bot.cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("migrate", help="apply migrations and the job-queue schema")
    p.add_argument("--alembic-ini", default="alembic.ini")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser(
        "ingest-dir", help="bulk-ingest every supported file in a folder"
    )
    p.add_argument("folder")
    p.add_argument("--doc-type", default="general")
    p.add_argument("--recursive", action="store_true")
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip files whose name or relative path matches (repeatable)",
    )
    p.set_defaults(func=cmd_ingest_dir)

    p = sub.add_parser("eval", help="run eval/evalset.csv")
    p.add_argument("mode", choices=["retrieval", "answer"])
    p.add_argument(
        "--judge", action="store_true", help="answer mode: add LLM-judge metrics"
    )
    p.add_argument(
        "--save", action="store_true", help="store the run (visible in the admin panel)"
    )
    p.add_argument("--json", help="also write metrics + results to this file")
    p.add_argument(
        "--min-hit-rate", type=float, help="fail if retrieval hit_rate is lower"
    )
    p.add_argument(
        "--min-pass-rate", type=float, help="fail if answer pass_rate is lower"
    )
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("purge-history", help="delete chat history past retention")
    p.set_defaults(func=cmd_purge_history)

    p = sub.add_parser("gen-secrets", help="print random secrets for .env")
    p.set_defaults(func=cmd_gen_secrets)

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
