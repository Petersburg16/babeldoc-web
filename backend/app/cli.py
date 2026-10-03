"""管理命令：uv run python -m app.cli <command> --help"""

from __future__ import annotations

import argparse
import getpass
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import func, select

from . import pdf_asset_sync
from .config import PROJECT_DIR, load_config
from .db import init_db, make_engine, make_sessionmaker
from .engine import build_spec, child_env, engine_command, spawn
from .job_ops import default_model
from .models import Job, ModelProfile, User
from .schemas import _normalize_username
from .security import SecretBox, hash_password


def _session():
    config = load_config()
    engine = make_engine(config.db_path)
    init_db(engine)
    return config, make_sessionmaker(engine)()


def _read_password(given: str | None) -> str:
    if given:
        return given
    first = getpass.getpass("新密码（至少 8 位）：")
    if first != getpass.getpass("再输入一次："):
        sys.exit("两次输入不一致")
    return first


def create_admin(args: argparse.Namespace) -> None:
    username = _normalize_username(args.username)
    password = _read_password(args.password)
    if len(password) < 8:
        sys.exit("密码至少 8 位")
    _config, db = _session()
    with db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(username=username, display_name=username, password_hash=hash_password(password), role="admin")
            db.add(user)
            print(f"已创建管理员 {username}")
        else:
            user.password_hash = hash_password(password)
            user.role = "admin"
            user.is_active = True
            print(f"用户 {username} 已存在：已设为管理员并重置密码")
        db.commit()


def reset_password(args: argparse.Namespace) -> None:
    password = _read_password(args.password)
    _config, db = _session()
    with db:
        user = db.scalar(select(User).where(User.username == args.username.lower()))
        if user is None:
            sys.exit(f"没有用户 {args.username}")
        user.password_hash = hash_password(password)
        db.commit()
    print("密码已重置")


def delete_user(args: argparse.Namespace) -> None:
    config, db = _session()
    with db:
        user = db.scalar(select(User).where(User.username == args.username.lower()))
        if user is None:
            sys.exit(f"没有用户 {args.username}")
        job_ids = db.scalars(select(Job.id).where(Job.user_id == user.id)).all()
        active = db.scalar(
            select(func.count(Job.id)).where(Job.user_id == user.id, Job.status.in_(("queued", "running")))
        )
        if active:
            sys.exit(f"{args.username} 还有 {active} 个排队/进行中的任务，请先在管理后台取消")
        db.delete(user)
        db.commit()
    for job_id in job_ids:
        shutil.rmtree(config.jobs_dir / job_id, ignore_errors=True)
    print(f"已删除用户 {args.username} 及其 {len(job_ids)} 个任务")


def engine_check(args: argparse.Namespace) -> None:
    config, db = _session()
    pdf = Path(args.pdf).resolve()
    if not pdf.is_file():
        sys.exit(f"找不到 {pdf}")
    work = Path(tempfile.mkdtemp(prefix="bdw-check-"))
    shutil.copyfile(pdf, work / "input.pdf")
    api_key = ""
    with db:
        if args.real:
            profile = default_model(db)
            if profile is None:
                sys.exit("--real 需要先在管理后台配置一个可用模型")
            api_key = SecretBox(config.data_dir / "secret.key").decrypt(profile.api_key_enc)
            db.expunge(profile)
        else:
            profile = ModelProfile(
                name="check",
                base_url="http://127.0.0.1:9/v1",
                model="dummy",
                qps=4,
                send_temperature=True,
                json_mode=False,
            )
    job = Job(
        id="check",
        user_id=0,
        filename=pdf.name,
        pages=args.pages,
        billed_pages=0,
        lang_in=args.lang_in,
        lang_out=args.lang_out,
        options={"auto_extract_glossary": bool(args.real)},
    )
    spec = build_spec(
        job=job,
        profile=profile,
        job_dir=work,
        watermark_mode="no_watermark",
        skip_translation=not args.real,
        mock={"seconds": 3} if config.engine == "mock" else None,
    )
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"engine={config.engine}  cmd={' '.join(engine_command(config))}")
    print(f"workdir={work}  skip_translation={not args.real}")
    started = time.monotonic()
    ep = spawn(config, spec_path, work / "engine.log", child_env(api_key))
    last_stage = None
    result = None
    assert ep.proc.stdout is not None
    for raw in ep.proc.stdout:
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        kind = event.get("event")
        if kind == "progress" and event.get("stage") != last_stage:
            last_stage = event.get("stage")
            print(f"  [{event.get('overall', 0):6.2f}%] {last_stage}")
        elif kind in ("started", "finished", "failed"):
            print(f"  {kind}: {json.dumps({k: v for k, v in event.items() if k != 'event'}, ensure_ascii=False)}")
            result = event if kind != "started" else result
    code = ep.proc.wait()
    ep.close()
    print(f"exit={code}  elapsed={time.monotonic() - started:.1f}s  log={work / 'engine.log'}")
    if not result or result.get("event") != "finished":
        sys.exit(1)


def pdf_assets_lock(args: argparse.Namespace) -> None:
    frontend = PROJECT_DIR / "frontend"
    lock = pdf_asset_sync.build_lock(
        frontend / "pdf-assets.json",
        frontend / "package-lock.json",
        frontend / "node_modules",
        frontend / "node_modules" / ".cache" / "pdf-assets",
    )
    out = frontend / "pdf-assets.lock.json"
    out.write_bytes((json.dumps(lock, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for engine_id, e in lock["engines"].items():
        print(f"  {engine_id:<14} {e['version']}  {len(e['files']):>4} 个文件  下载约 {e['transfer'] / 1e6:6.1f} MB")
    print(f"已写入 {out.name}")


def pdf_assets_sync(args: argparse.Namespace) -> None:
    lock = Path(args.lock) if args.lock else PROJECT_DIR / "frontend" / "pdf-assets.lock.json"
    dest = Path(args.dest) if args.dest else load_config().pdf_assets_dir
    pdf_asset_sync.sync(lock, dest, seeds=[Path(s) for s in args.seed], registry=args.registry, keep=args.keep)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("create-admin", help="创建管理员（已存在则提升为管理员并重置密码）")
    p.add_argument("username")
    p.add_argument("--password")
    p.set_defaults(func=create_admin)

    p = sub.add_parser("reset-password", help="重置任意用户密码")
    p.add_argument("username")
    p.add_argument("--password")
    p.set_defaults(func=reset_password)

    p = sub.add_parser("delete-user", help="删除用户及其全部任务文件")
    p.add_argument("username")
    p.set_defaults(func=delete_user)

    p = sub.add_parser("engine-check", help="用一个 PDF 跑通引擎（默认跳过翻译，不耗 token）")
    p.add_argument("pdf")
    p.add_argument("--real", action="store_true", help="用默认模型真实翻译")
    p.add_argument("--pages", default="1-2")
    p.add_argument("--lang-in", default="en")
    p.add_argument("--lang-out", default="zh-CN")
    p.set_defaults(func=engine_check)

    p = sub.add_parser("pdf-assets", help="PDF 工具运行时资源：lock 生成锁文件（开发机），sync 按锁文件下载")
    psub = p.add_subparsers(required=True)
    q = psub.add_parser("lock", help="按 frontend/pdf-assets.json 和 node_modules 生成 frontend/pdf-assets.lock.json")
    q.set_defaults(func=pdf_assets_lock)
    q = psub.add_parser("sync", help="按锁文件下载、校验并预压缩到 BDW_PDF_ASSETS_DIR（或 --dest）")
    q.add_argument("--lock", help="锁文件路径，默认 frontend/pdf-assets.lock.json")
    q.add_argument("--dest", help="目标目录，默认 BDW_PDF_ASSETS_DIR（数据目录下的 pdf-assets）")
    q.add_argument("--seed", action="append", default=[], help="可复用的本地目录（同名同哈希直接硬链接），可多次指定")
    q.add_argument("--registry", help="npm 源，默认 https://registry.npmjs.org/，也可用 BDW_NPM_REGISTRY")
    q.add_argument("--keep", type=int, default=2, help="每个引擎保留最近几个版本，默认 2")
    q.set_defaults(func=pdf_assets_sync)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
