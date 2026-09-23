from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..utils.srt import Cue, parse_ts

# Trích NGUYÊN công thức từ assemble.py (assemble_project/assemble_multi) —
# 2 nơi đó tính y hệt nhau, chỉ khác điểm khởi đầu offset trên timeline
# nhiều-tập. Tách ra đây để cả CapCut (assemble.py) lẫn xuất video trực tiếp
# (export_direct.py) dùng chung 1 nguồn công thức, không lệch nhau.
#
# Câu tiếng Việt luôn cần nhiều âm tiết hơn câu tiếng Trung tương ứng để đọc
# cùng 1 ý — TTS thường dài hơn khung [start,end] gốc của câu. Bù giờ theo 2
# bước, ưu tiên bước 1:
#   1) Cho VIDEO (+ audio đi kèm) chạy chậm lại đúng đoạn đó, tối đa
#      `min_video_speed` — gần như không nhận ra đang tua chậm.
#   2) Nếu chậm video hết mức vẫn chưa đủ bù, phần còn thiếu mới tăng tốc
#      giọng đọc, tối đa `max_voice_speedup`.
MIN_VIDEO_SPEED = 0.85
MAX_VOICE_SPEEDUP = 1.8


@dataclass
class TimelinePlan:
    # (source_start_us, source_dur_us, speed) trên video GỐC, theo đúng thứ
    # tự cần dựng lên timeline mới — TƯƠNG ĐỐI (không cộng offset), người gọi
    # tự cộng dồn timeline_cursor bắt đầu từ 0 hoặc từ 1 offset cho trước
    # (xem assemble_multi — ráp nhiều tập nối tiếp).
    video_plan: list[tuple[int, int, float]]
    # cue_id -> (start_us, end_us) TƯƠNG ĐỐI (offset=0) — mốc giọng đọc THẬT
    # SỰ chiếm trên timeline đã giãn, dùng cho cả audio lẫn phụ đề (không phải
    # [cue.start, cue.end] gốc — 2 khung này thường lệch nhau).
    voice_placement: dict[int, tuple[int, int]] = field(default_factory=dict)
    # cue_id -> tốc độ tăng tốc giọng đọc đã áp (1.0 = giữ nguyên)
    voice_speed: dict[int, float] = field(default_factory=dict)
    # cue_id -> thời lượng file TTS gốc (chưa tăng tốc) — cache lại để người
    # gọi khỏi phải đo lại (vd export_direct.py cần đúng giá trị này để tính
    # duration thật của đoạn atempo trong ffmpeg).
    tts_duration_us: dict[int, int] = field(default_factory=dict)
    # Tổng thời lượng timeline sau khi giãn (TƯƠNG ĐỐI, tính từ 0) — cộng dồn
    # vào offset của tập kế tiếp khi ráp nhiều tập.
    total_dur_us: int = 0
    # cảnh báo trôi timestamp tối đa (giây) — vượt quá cả 2 mức bù thì đẩy
    # lùi câu sau, không còn khớp mốc gốc nữa.
    drift_us: int = 0
    # Lưu lại để gọi `map_time()` bên ngoài — dùng khi 1 cue không có audio
    # TTS (lỗi/rỗng) nên không có trong `voice_placement`, phải tự quy đổi
    # [cue.start, cue.end] gốc sang mốc trên timeline đã giãn.
    stretch_intervals: list[tuple[int, int, float]] = field(default_factory=list)


def map_time(stretch_intervals: list[tuple[int, int, float]], t_us: int) -> int:
    """Quy đổi 1 mốc thời gian trên video GỐC sang mốc tương ứng trên
    timeline đã giãn (do các khoảng bị chậm lại phía trước) — hàm thuần,
    dùng `TimelinePlan.stretch_intervals` trả về từ `compute_timeline_plan`."""
    shift = 0
    for s, e, spd in stretch_intervals:
        if t_us <= s:
            break
        if t_us >= e:
            orig = e - s
            shift += round(orig / spd) - orig
        else:
            orig = e - s
            frac = (t_us - s) / orig if orig else 0.0
            new_full = round(orig / spd)
            shift += round(new_full * frac) - round(orig * frac)
            break
    return t_us + shift


def _sec_to_us(seconds: float, sec_per_unit: int) -> int:
    return round(seconds * sec_per_unit)


def compute_timeline_plan(
    manifest: list[dict],
    vi_cues: list[Cue],
    video_duration_us: int,
    audio_dir: Path,
    sec_per_unit: int,
    audio_duration_us_fn,
    min_video_speed: float = MIN_VIDEO_SPEED,
    max_voice_speedup: float = MAX_VOICE_SPEEDUP,
) -> TimelinePlan:
    """`audio_duration_us_fn(path: Path) -> int` đo thời lượng 1 file TTS
    (đơn vị `sec_per_unit`/giây) — tách ra tham số vì CapCut dùng
    `cc.AudioMaterial(...).duration` (đơn vị `cc.SEC`) còn ffmpeg dùng
    `ffprobe` (thường đo bằng micro-giây, `sec_per_unit=1_000_000`)."""

    def sec_to_us(seconds: float) -> int:
        return _sec_to_us(seconds, sec_per_unit)

    # --- Bước 1: với mỗi câu có TTS, tính xem video có cần chậm lại không để
    # câu đó có thêm thời gian (tối đa min_video_speed). Tốc độ GIỌNG ĐỌC
    # KHÔNG tính ở đây — để dành tính ở Bước 3. ---
    tts_windows: dict[int, dict] = {}
    for entry in manifest:
        if not entry.get("path"):
            continue
        audio_path = audio_dir / entry["path"]
        tts_dur_us = audio_duration_us_fn(audio_path)
        wanted_start_us = sec_to_us(parse_ts(entry["start"]))
        wanted_end_us = sec_to_us(parse_ts(entry["end"]))
        window_us = max(wanted_end_us - wanted_start_us, 1)

        needed_speed_full = tts_dur_us / window_us
        if needed_speed_full <= 1.0:
            video_speed = 1.0
        else:
            ideal_video_speed = window_us / tts_dur_us
            video_speed = max(ideal_video_speed, min_video_speed)

        tts_windows[entry["id"]] = {
            "start_us": wanted_start_us,
            "end_us": wanted_end_us,
            "tts_dur_us": tts_dur_us,
            "video_speed": video_speed,
        }

    # Chỉ những câu THẬT SỰ cần chậm (video_speed < 1.0) mới tạo "khoảng
    # giãn". Kẹp start >= end của khoảng trước để phòng hờ 2 cue kề nhau
    # chồng biên nhỏ.
    stretch_intervals: list[tuple[int, int, float]] = []
    for w in sorted(tts_windows.values(), key=lambda w: w["start_us"]):
        if w["video_speed"] < 1.0:
            s = max(w["start_us"], stretch_intervals[-1][1] if stretch_intervals else 0)
            if w["end_us"] > s:
                stretch_intervals.append((s, w["end_us"], w["video_speed"]))

    def _map(t_us: int) -> int:
        return map_time(stretch_intervals, t_us)

    # --- Bước 2: dựng plan video/nhạc nền theo `stretch_intervals` — đoạn
    # cần bù giờ chậm lại, đoạn khác giữ nguyên tốc độ. ---
    plan: list[tuple[int, int, float]] = []
    cursor = 0
    for s, e, spd in stretch_intervals:
        s = max(s, cursor)
        if e <= s:
            continue
        if s > cursor:
            plan.append((cursor, s - cursor, 1.0))
        plan.append((s, e - s, spd))
        cursor = e
    if cursor < video_duration_us:
        plan.append((cursor, video_duration_us - cursor, 1.0))

    timeline_cursor_us = 0
    for source_start_us, source_dur_us, spd in plan:
        source_dur_us = min(source_dur_us, video_duration_us - source_start_us)
        if source_dur_us <= 0:
            continue
        new_dur_us = round(source_dur_us / spd) if spd != 1.0 else source_dur_us
        timeline_cursor_us += new_dur_us
    total_dur_us_new = timeline_cursor_us

    # --- Bước 3: giọng đọc TTS — đặt tại mốc ĐÃ QUY ĐỔI qua map_time(). Chỉ
    # tăng tốc giọng ĐÚNG BẰNG mức cần thiết để không đè lên mốc bắt đầu dự
    # kiến của câu TTS kế tiếp (nhìn trước 1 câu). ---
    tts_order = [
        {**tts_windows[entry["id"]], "entry": entry, "start_us": _map(tts_windows[entry["id"]]["start_us"])}
        for entry in manifest
        if entry.get("path")
    ]

    cursor_us = 0
    drift_us = 0
    voice_placement: dict[int, tuple[int, int]] = {}
    voice_speed: dict[int, float] = {}
    tts_duration_us: dict[int, int] = {}
    for i, item in enumerate(tts_order):
        start_us = max(item["start_us"], cursor_us)
        if start_us > item["start_us"]:
            drift_us = max(drift_us, start_us - item["start_us"])

        next_start_us = tts_order[i + 1]["start_us"] if i + 1 < len(tts_order) else total_dur_us_new
        available_us = max(next_start_us - start_us, 1)
        speed = min(max(item["tts_dur_us"] / available_us, 1.0), max_voice_speedup)

        dur_us = round(item["tts_dur_us"] / speed) if speed > 1.0 else item["tts_dur_us"]
        cue_id = item["entry"]["id"]
        voice_placement[cue_id] = (start_us, start_us + dur_us)
        voice_speed[cue_id] = speed
        tts_duration_us[cue_id] = item["tts_dur_us"]
        cursor_us = start_us + dur_us

    return TimelinePlan(
        video_plan=plan,
        voice_placement=voice_placement,
        voice_speed=voice_speed,
        tts_duration_us=tts_duration_us,
        total_dur_us=total_dur_us_new,
        drift_us=drift_us,
        stretch_intervals=stretch_intervals,
    )
