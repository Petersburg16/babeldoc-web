"""deploy/remote.sh 里备份与恢复用的 Python 片段（DB_TOOL）。

它写在 bash 的单引号字符串里，代码里一出现单引号，bash 就会把字符串截断，Python 拿到的是被改坏的代码。
这里按 bash 的规则取出这段代码，对测试库实际跑一遍快照和校验。
"""

import subprocess
import sys
from pathlib import Path

REMOTE_SH = Path(__file__).resolve().parents[2] / "deploy" / "remote.sh"
START = "\nDB_TOOL='"


def db_tool_source() -> str:
    text = REMOTE_SH.read_text(encoding="utf-8")
    start = text.index(START) + len(START)
    end = text.index("'", start)  # bash 单引号里没有转义：遇到的第一个单引号就结束字符串
    assert text[end:].startswith("'\n"), "DB_TOOL 的 Python 代码里不能出现单引号"
    return text[start:end]


def run_tool(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", db_tool_source(), *args], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_snapshot_and_verify(admin_client, config, tmp_path):
    snapshot = tmp_path / "snapshot.db"
    summary = run_tool("snapshot", str(config.db_path), str(snapshot))
    assert summary.startswith("ok ")
    assert "users=1" in summary
    assert "model_profiles=1" in summary
    assert "meeting_llm_models=1" in summary
    assert "meeting_llm_presets=1" in summary

    # 恢复前的校验：本机 secret.key 能解开翻译模型和会议模型的两个 Key
    summary = run_tool("verify", str(snapshot), str(config.data_dir / "secret.key"))
    assert summary.startswith("ok ")
    assert summary.endswith("keys=2/2")
