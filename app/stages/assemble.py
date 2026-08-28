from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from .. import config
from ..utils.srt import Cue, parse_ts

VIDEO_TRACK = "video"
BACKGROUND_TRACK = "background"
VOICE_TRACK = "voice"
TEXT_TRACK = "titles"

# Nhạc nền/SFX gốc (đã tách bằng demucs) — giữ khá rõ nhưng hạ nhẹ để không
# át giọng đọc TTS đang chạy song song trên track riêng.
BACKGROUND_VOLUME = 0.8

# Giới hạn tăng tốc giọng đọc khi TTS dài hơn khung câu gốc (thoại tiếng Trung
# dồn dập, câu tiếng Việt luôn cần nhiều âm tiết hơn để đọc cùng ý — xem
# docstring assemble_project). CapCut TTS bỏ qua tham số `rate` (đã tự test —
# 1.0/1.3/1.5 ra cùng 1 độ dài) nên chỉ còn cách nén audio phía draft. Đã thử
# 1.3x (ưu tiên tự nhiên, còn trễ tới 7.17s ở đoạn quảng cáo/game dồn dập) —
# người dùng chọn ưu tiên khớp cảnh hơn, chấp nhận giọng gấp hơn ở đoạn đó.
MAX_VOICE_SPEEDUP = 1.8

_SUBTITLE_TRANSFORM_Y = -0.78  # đẩy xuống gần đáy khung hình


class AssembleError(RuntimeError):
    pass


def _sec_to_us(seconds: float, sec_per_unit: int) -> int:
    return round(seconds * sec_per_unit)


def _patch_text_line_max_width(text_segment_cls) -> None:
    """pycapcut.TextSegment.export_material hard-code
    force_apply_line_max_width=False nên TextStyle.max_line_width khai báo
    bao nhiêu cũng không có tác dụng thật trong CapCut — bug đã được xác nhận
    (ảnh chụp thật) ở D:\\CapcutSupperTool, dự án khác cùng dùng pycapcut trên
    máy này. Vá lại 1 lần, không sửa file cài trong venv."""
    if getattr(text_segment_cls.export_material, "_reup_patched", False):
        return

    original = text_segment_cls.export_material

    def patched(self):
        data = original(self)
        data["force_apply_line_max_width"] = True
        return data

    patched._reup_patched = True
    text_segment_cls.export_material = patched


def assemble_project(
    video_path: Path,
    background_path: Path,
    manifest: list[dict],
    vi_cues: list[Cue],
    audio_dir: Path,
    draft_name: str,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> Path:
    """Ráp video gốc (tắt tiếng thoại) + nhạc nền/SFX đã tách + giọng đọc TTS
    + phụ đề việt thành 1 draft CapCut, ghi thẳng vào thư mục draft thật của
    CapCut (`config.capcut_drafts_dir()`) để mở lên là thấy ngay.

    `manifest`: list [{id, start, end, path, duration_ms, error}] từ
    `tts.tts_project()`. `vi_cues`: cues từ `sub_vi.srt` (dùng cho phụ đề,
    ĐÚNG khung thời gian video gốc, không phụ thuộc TTS đọc dài/ngắn).
    """
    if not video_path.exists():
        raise AssembleError(f"Không thấy video: {video_path}")
    if not background_path.exists():
        raise AssembleError(f"Không thấy audio nền đã tách: {background_path}")

    import pycapcut as cc

    SEC = cc.SEC
    _patch_text_line_max_width(cc.TextSegment)

    def sec_to_us(seconds: float) -> int:
        return _sec_to_us(seconds, SEC)

    out_root = config.capcut_drafts_dir()
    folder = cc.DraftFolder(str(out_root))

    video_material = cc.VideoMaterial(str(video_path))
    script = folder.create_draft(draft_name, video_material.width, video_material.height, fps=30, allow_replace=True)
    script.add_track(cc.TrackType.video, VIDEO_TRACK)
    script.add_track(cc.TrackType.audio, BACKGROUND_TRACK)
    script.add_track(cc.TrackType.audio, VOICE_TRACK)
    script.add_track(cc.TrackType.text, TEXT_TRACK)

    total_steps = 3 + len(manifest) + len(vi_cues)
    done = 0

    def step(label: str) -> None:
        nonlocal done
        done += 1
        if on_progress:
            on_progress(done, total_steps, label)

    # --- Video gốc, tắt hẳn tiếng thoại (volume=0) — hình ảnh giữ nguyên ---
    video_seg = cc.VideoSegment(video_material, cc.Timerange(0, video_material.duration), volume=0.0)
    script.add_segment(video_seg, track_name=VIDEO_TRACK)
    step("video gốc")

    # --- Nhạc nền/SFX đã tách bằng demucs, thay cho thoại gốc ---
    bg_material = cc.AudioMaterial(str(background_path))
    bg_seg = cc.AudioSegment(bg_material, cc.Timerange(0, bg_material.duration), volume=BACKGROUND_VOLUME)
    script.add_segment(bg_seg, track_name=BACKGROUND_TRACK)
    step("nhạc nền")

    # --- Giọng đọc TTS — đặt tại mốc câu gốc. Câu tiếng Việt thường cần đọc
    # lâu hơn câu tiếng Trung tương ứng (nhiều âm tiết hơn cho cùng 1 ý,
    # thoại dồn dập càng rõ) — nếu audio TTS dài hơn khung [start,end] gốc
    # của câu, tăng tốc phát lại (pycapcut `speed`, tự nén target_timerange,
    # KHÔNG cần tạo lại TTS) tối đa `MAX_VOICE_SPEEDUP` để cố khớp lại đúng
    # khung. Vượt quá mức đó thì chấp nhận còn dư, đẩy lùi câu sau để không
    # chồng segment (pycapcut báo lỗi cứng nếu chồng) — phần dư này mới thật
    # sự là "trôi timestamp" cần Stage 6 (review thủ công) kiểm lại.
    cursor_us = 0
    drift_us = 0
    speedup_count = 0
    for entry in manifest:
        if entry.get("path"):
            audio_path = audio_dir / entry["path"]
            material = cc.AudioMaterial(str(audio_path))
            wanted_start_us = sec_to_us(parse_ts(entry["start"]))
            wanted_end_us = sec_to_us(parse_ts(entry["end"]))
            window_us = max(wanted_end_us - wanted_start_us, 1)

            needed_speed = material.duration / window_us
            speed = min(max(needed_speed, 1.0), MAX_VOICE_SPEEDUP)
            speedup_count += speed > 1.0

            start_us = max(wanted_start_us, cursor_us)
            if start_us > wanted_start_us:
                drift_us = max(drift_us, start_us - wanted_start_us)

            if speed > 1.0:
                seg = cc.AudioSegment(
                    material,
                    cc.Timerange(start_us, 0),
                    source_timerange=cc.Timerange(0, material.duration),
                    speed=speed,
                )
            else:
                seg = cc.AudioSegment(material, cc.Timerange(start_us, material.duration))
            script.add_segment(seg, track_name=VOICE_TRACK)
            cursor_us = start_us + seg.target_timerange.duration
        step(f"voice câu {entry['id']}")

    if speedup_count:
        logger.info("assemble {}: tăng tốc {} câu (tối đa {}x) để khớp khung gốc", draft_name, speedup_count, MAX_VOICE_SPEEDUP)
    if drift_us > 0:
        logger.warning(
            "assemble {}: TTS vẫn trôi tối đa {:.2f}s so với timestamp gốc sau khi đã tăng tốc — kiểm lại ở CapCut",
            draft_name,
            drift_us / SEC,
        )

    # --- Phụ đề — đúng khung thời gian câu gốc, KHÔNG phụ thuộc TTS dài/ngắn.
    # sub_vi.srt đôi khi có 2 cue chồng vài trăm mili-giây ở ranh giới 2 khối
    # Whisper kề nhau (dedup ở transcribe.py chỉ bắt trùng lặp lớn >50%, không
    # bắt chồng biên nhỏ) — pycapcut báo lỗi cứng SegmentOverlap nếu chồng,
    # nên tự đẩy lùi giống cách xử lý voice track ở trên.
    style = cc.TextStyle(size=6.0, color=(1.0, 1.0, 1.0), align=1, max_line_width=0.82)
    border = cc.TextBorder(color=(0.0, 0.0, 0.0), width=40.0)
    text_cursor_us = 0
    for cue in vi_cues:
        text = (cue.text or "").strip()
        if text:
            start_us = max(sec_to_us(parse_ts(cue.start)), text_cursor_us)
            end_us = sec_to_us(parse_ts(cue.end))
            dur_us = max(end_us - start_us, 1)
            clip = cc.ClipSettings(transform_y=_SUBTITLE_TRANSFORM_Y)
            seg = cc.TextSegment(text, cc.Timerange(start_us, dur_us), style=style, clip_settings=clip, border=border)
            script.add_segment(seg, track_name=TEXT_TRACK)
            text_cursor_us = start_us + dur_us
        step(f"phụ đề câu {cue.id}")

    script.save()
    return out_root / draft_name / "draft_content.json"
