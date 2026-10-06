"""备份与恢复用的 python -m app.cli db snapshot / verify（deploy/remote.sh 的 backup、restore 调用）。"""

import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet

from app import cli
from app.config import BACKEND_DIR


def run_db(*args: str) -> str:
    """像备份定时器那样调用：只给 PATH（Windows 另需 SYSTEMROOT），不设 HOME，也不设任何 BDW_* 变量。"""
    env = {"PATH": os.environ["PATH"]}
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "db", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def stray_files() -> set[Path]:
    # 没设 BDW_DATA_DIR 时缺省数据目录是 backend/data：一旦调用了 SecretBox 或 init_db，就会在这里生成文件
    return {p for d in (BACKEND_DIR, BACKEND_DIR / "data") for n in ("secret.key", "app.db") if (p := d / n).exists()}


def test_snapshot_and_verify_without_env(admin_client, config, tmp_path):
    snapshot = tmp_path / "snapshot.db"
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    strays = stray_files()

    summary = run_db("snapshot", str(config.db_path), str(snapshot))
    assert summary.startswith("ok ")
    assert "users=1" in summary
    assert "model_profiles=1" in summary
    assert "meeting_llm_models=1" in summary
    assert "meeting_llm_presets=1" in summary

    # 恢复前的校验：本机 secret.key 能解开翻译模型和会议模型的两个 Key
    summary = run_db("verify", str(snapshot), str(config.data_dir / "secret.key"))
    assert summary.startswith("ok ")
    assert summary.endswith("keys=2/2")
    assert "keys=" not in run_db("verify", str(snapshot))

    # 只多出快照本身：没有在别处生成 secret.key 或 app.db
    assert {p for p in tmp_path.rglob("*") if p.is_file()} == before | {snapshot}
    assert stray_files() == strays


def test_verify_reports_keys_the_local_secret_cannot_open(admin_client, config, tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("db 子命令的路径都由参数给出，不能读配置、跑迁移或生成密钥")

    for name in ("load_config", "init_db", "SecretBox"):
        monkeypatch.setattr(cli, name, forbidden)
    other_key = tmp_path / "other.key"
    other_key.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(sys, "argv", ["app.cli", "db", "verify", str(config.db_path), str(other_key)])

    cli.main()
    summary = capsys.readouterr().out.strip()
    assert summary.startswith("ok users=1 ")
    assert summary.endswith("keys=0/2（2 个密钥用本机 secret.key 解不开，恢复后要在后台重新填写）")
