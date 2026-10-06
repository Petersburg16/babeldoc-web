from __future__ import annotations

from app.meeting.manager import _new_part, raw_name, reset_for_retry


def test_reset_for_retry(tmp_path):
    submitted = {
        **_new_part(1, 0, 1000, "part-1.mp3"),
        "state": "failed",
        "task_id": "t1",
        "token": "x",
        "error": "断网",
    }

    # 断网、被一起取消：保留任务号接着查，不重新提交（重复计费）
    assert reset_for_retry(submitted, tmp_path) == {**submitted, "state": "submitted", "error": None}

    # 服务商明确失败：清掉任务号和令牌，重新提交
    final = {**submitted, "final": True, "submitted_at": "2026-10-01T00:00:00"}
    assert reset_for_retry(final, tmp_path) == _new_part(1, 0, 1000, "part-1.mp3")

    # 结果已经落盘：算完成，哪怕标着 final
    (tmp_path / raw_name(1)).write_text("{}", "utf-8")
    assert reset_for_retry(final, tmp_path) == {**final, "state": "done", "raw": "asr-1.json", "error": None}
