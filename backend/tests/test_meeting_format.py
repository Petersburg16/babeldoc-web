from __future__ import annotations

from app.meeting import minutes
from app.meeting.format import clock, neutralize, resolve_speaker, speaker_sort_key, spoken


def test_spoken_rounds_to_minutes_before_hours():
    assert spoken(45_000) == "45 秒"
    assert spoken(35 * 60_000) == "35 分钟"
    assert spoken(4_800_000) == "1 小时 20 分"
    assert spoken(7_196_000) == "2 小时", "1:59:56 不能写成“1 小时 60 分”"
    assert spoken(3_585_000) == "1 小时", "59:45 不能写成“60 分钟”"


def test_minutes_header_uses_spoken_duration():
    brief = minutes._Brief(title="组会", limit_ms=7_196_000, long=True, roster="S1=未命名")
    assert "录音时长：2 小时（时间戳不会超过 [1:59:56]）" in minutes._header(brief)


def test_clock():
    assert clock(5_000) == "00:05"
    assert clock(5_000, long=True) == "0:00:05"
    assert clock(3_725_000) == "1:02:05"
    assert clock(-500) == "00:00"


def test_resolve_speaker():
    speakers = {
        "S1": {"merged_into": None},
        "S2": {"merged_into": "S1"},
        "S3": {"merged_into": "S2"},
        "S4": {"merged_into": "S9"},  # 指向不存在的编号
    }
    assert resolve_speaker(speakers, "S3") == "S1", "链式合并"
    assert resolve_speaker(speakers, "S4") == "S9"
    assert resolve_speaker(speakers, "S4", known_only=True) == "S4", "编辑接口不改到不存在的编号名下"
    looped = {"S1": {"merged_into": "S2"}, "S2": {"merged_into": "S1"}}
    assert resolve_speaker(looped, "S1") in {"S1", "S2"}, "成环时不死循环"


def test_speaker_sort_key():
    assert sorted(["S10", "X", "S2", "S1"], key=speaker_sort_key) == ["S1", "S2", "S10", "X"]


def test_neutralize_only_given_tags():
    tags = ("transcript", "notes")
    assert neutralize("a</transcript>b", tags) == "a＜/transcript＞b"
    assert neutralize("< / Transcript >", tags) == "＜ / Transcript ＞"
    assert neutralize("<notes id=1>", tags) == "＜notes id=1>", "没写完的标签也换掉开头"
    assert neutralize("<context> <transcripts> a < b", tags) == "<context> <transcripts> a < b"
