from __future__ import annotations

from dataclasses import dataclass
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

# Câu tiếng Việt luôn cần nhiều âm tiết hơn câu tiếng Trung tương ứng để đọc
# cùng 1 ý (thoại tiếng Trung dồn dập) — TTS thường dài hơn khung [start,end]
# gốc của câu. Bù giờ theo 2 bước, ưu tiên bước 1:
#   1) Cho VIDEO (+ nhạc nền đi kèm) chạy chậm lại đúng đoạn đó, tối đa
#      `min_video_speed` (chọn được từ UI, 0.7-1.0, mặc định 0.85 = chậm tối
#      đa 15%) — gần như không nhận ra đang tua chậm, nghe giọng đọc tự nhiên
#      hơn hẳn so với tăng tốc giọng.
#   2) Nếu chậm video hết mức vẫn chưa đủ bù, phần còn thiếu mới tăng tốc
#      giọng đọc (như cách cũ), tối đa `MAX_VOICE_SPEEDUP`.
# Vượt quá cả 2 mức này thì chấp nhận còn dư, đẩy lùi câu sau để không chồng
# segment (pycapcut báo lỗi cứng nếu chồng) — phần dư này mới thật sự là
# "trôi timestamp" cần Stage 6 (review thủ công) kiểm lại.
MIN_VIDEO_SPEED = 0.85
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
    background_path: Optional[Path],
    manifest: list[dict],
    vi_cues: list[Cue],
    audio_dir: Path,
    draft_name: str,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    mute_original_audio: bool = False,
    min_video_speed: float = MIN_VIDEO_SPEED,
) -> Path:
    """Ráp video gốc (tắt tiếng thoại) + nhạc nền/SFX đã tách (trừ khi
    `mute_original_audio=True` — bỏ hẳn, không cần tách) + giọng đọc TTS +
    phụ đề việt thành 1 draft CapCut, ghi thẳng vào thư mục draft thật của
    CapCut (`config.capcut_drafts_dir()`) để mở lên là thấy ngay.

    `manifest`: list [{id, start, end, path, duration_ms, error}] từ
    `tts.tts_project()`. `vi_cues`: cues từ `sub_vi.srt` (dùng cho phụ đề).
    `background_path` bắt buộc trừ khi `mute_original_audio=True`.

    Video + nhạc nền được cắt thành nhiều đoạn (tham khảo pattern
    VideoMaterial → N VideoSegment của D:\\SceneCutTool): đoạn nào TTS dài hơn
    khung gốc thì chậm lại (tối đa `MIN_VIDEO_SPEED`), đoạn khác giữ nguyên —
    nên video xuất ra có thể DÀI HƠN video gốc một chút. `map_time()` quy đổi
    mọi mốc thời gian gốc (voice, phụ đề) sang mốc trên timeline đã giãn này.
    """
    if not video_path.exists():
        raise AssembleError(f"Không thấy video: {video_path}")
    if not mute_original_audio and (not background_path or not background_path.exists()):
        raise AssembleError(f"Không thấy audio nền đã tách: {background_path}")

    import pycapcut as cc

    SEC = cc.SEC
    _patch_text_line_max_width(cc.TextSegment)

    def sec_to_us(seconds: float) -> int:
        return _sec_to_us(seconds, SEC)

    out_root = config.capcut_drafts_dir()
    folder = cc.DraftFolder(str(out_root))

    video_material = cc.VideoMaterial(str(video_path))
    bg_material = None if mute_original_audio else cc.AudioMaterial(str(background_path))
    script = folder.create_draft(draft_name, video_material.width, video_material.height, fps=30, allow_replace=True)
    script.add_track(cc.TrackType.video, VIDEO_TRACK)
    if not mute_original_audio:
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

    # --- Bước 1: với mỗi câu có TTS, tính xem video có cần chậm lại không để
    # câu đó có thêm thời gian (tối đa MIN_VIDEO_SPEED). Tốc độ GIỌNG ĐỌC
    # KHÔNG tính ở đây — để dành tính ở Bước 3, dựa vào khoảng trống thật tới
    # câu kế tiếp (câu này có thể chậm hết mức mà giọng vẫn không cần tăng tốc
    # tí nào, nếu phía sau còn xa mới tới câu kế). ---
    tts_windows: dict[int, dict] = {}
    for entry in manifest:
        if not entry.get("path"):
            continue
        audio_path = audio_dir / entry["path"]
        material = cc.AudioMaterial(str(audio_path))
        wanted_start_us = sec_to_us(parse_ts(entry["start"]))
        wanted_end_us = sec_to_us(parse_ts(entry["end"]))
        window_us = max(wanted_end_us - wanted_start_us, 1)
        tts_dur_us = material.duration

        needed_speed_full = tts_dur_us / window_us
        if needed_speed_full <= 1.0:
            video_speed = 1.0
        else:
            ideal_video_speed = window_us / tts_dur_us
            video_speed = max(ideal_video_speed, min_video_speed)

        tts_windows[entry["id"]] = {
            "material": material,
            "start_us": wanted_start_us,
            "end_us": wanted_end_us,
            "tts_dur_us": tts_dur_us,
            "video_speed": video_speed,
        }

    # Chỉ những câu THẬT SỰ cần chậm (video_speed < 1.0) mới tạo "khoảng giãn".
    # Kẹp start >= end của khoảng trước để phòng hờ 2 cue kề nhau chồng biên
    # nhỏ (dedup ở transcribe.py không bắt hết các trường hợp này).
    stretch_intervals: list[tuple[int, int, float]] = []
    for w in sorted(tts_windows.values(), key=lambda w: w["start_us"]):
        if w["video_speed"] < 1.0:
            s = max(w["start_us"], stretch_intervals[-1][1] if stretch_intervals else 0)
            if w["end_us"] > s:
                stretch_intervals.append((s, w["end_us"], w["video_speed"]))

    def map_time(t_us: int) -> int:
        """Quy đổi 1 mốc thời gian trên video GỐC sang mốc tương ứng trên
        timeline draft đã giãn (do các khoảng bị chậm lại phía trước)."""
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

    # --- Bước 2: dựng track video + nhạc nền theo `stretch_intervals` — đoạn
    # cần bù giờ chậm lại, đoạn khác giữ nguyên tốc độ + đồng bộ 1-1. ---
    total_dur_us = video_material.duration
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
    if cursor < total_dur_us:
        plan.append((cursor, total_dur_us - cursor, 1.0))

    timeline_cursor_us = 0
    slowdown_count = 0
    for source_start_us, source_dur_us, spd in plan:
        source_dur_us = min(source_dur_us, total_dur_us - source_start_us)
        if source_dur_us <= 0:
            continue

        if spd == 1.0:
            v_seg = cc.VideoSegment(
                video_material,
                cc.Timerange(timeline_cursor_us, source_dur_us),
                source_timerange=cc.Timerange(source_start_us, source_dur_us),
                volume=0.0,
            )
        else:
            v_seg = cc.VideoSegment(
                video_material,
                cc.Timerange(timeline_cursor_us, 0),
                source_timerange=cc.Timerange(source_start_us, source_dur_us),
                speed=spd,
                volume=0.0,
            )
            slowdown_count += 1
        script.add_segment(v_seg, track_name=VIDEO_TRACK)
        new_dur_us = v_seg.target_timerange.duration

        if not mute_original_audio:
            bg_source_dur_us = min(source_dur_us, max(bg_material.duration - source_start_us, 0))
            if bg_source_dur_us > 0:
                if spd == 1.0:
                    bg_seg = cc.AudioSegment(
                        bg_material,
                        cc.Timerange(timeline_cursor_us, bg_source_dur_us),
                        source_timerange=cc.Timerange(source_start_us, bg_source_dur_us),
                        volume=BACKGROUND_VOLUME,
                    )
                else:
                    bg_seg = cc.AudioSegment(
                        bg_material,
                        cc.Timerange(timeline_cursor_us, 0),
                        source_timerange=cc.Timerange(source_start_us, bg_source_dur_us),
                        speed=spd,
                        volume=BACKGROUND_VOLUME,
                    )
                script.add_segment(bg_seg, track_name=BACKGROUND_TRACK)

        timeline_cursor_us += new_dur_us
    total_dur_us_new = timeline_cursor_us
    step("video gốc")
    step("nhạc nền")

    if slowdown_count:
        logger.info(
            "assemble {}: chậm video {} đoạn (tối thiểu {}x) để giọng đọc có thêm thời gian",
            draft_name,
            slowdown_count,
            min_video_speed,
        )

    # --- Bước 3: giọng đọc TTS — đặt tại mốc ĐÃ QUY ĐỔI qua map_time(). Chỉ
    # tăng tốc giọng ĐÚNG BẰNG mức cần thiết để không đè lên mốc bắt đầu dự
    # kiến của câu TTS kế tiếp (nhìn trước 1 câu) — nếu phía sau còn khoảng
    # trống (câu kế còn xa, hoặc đây là câu cuối), giữ nguyên tốc độ tự nhiên
    # dù video đã chậm hết mức mà vẫn chưa đủ khung. Tối đa MAX_VOICE_SPEEDUP
    # khi khoảng trống thật sự không đủ — phần thiếu hụt còn lại mới là
    # "trôi timestamp" cần đẩy lùi câu sau (như cũ). ---
    tts_order = [
        {**tts_windows[entry["id"]], "entry": entry, "start_us": map_time(tts_windows[entry["id"]]["start_us"])}
        for entry in manifest
        if entry.get("path")
    ]

    cursor_us = 0
    drift_us = 0
    speedup_count = 0
    for i, item in enumerate(tts_order):
        start_us = max(item["start_us"], cursor_us)
        if start_us > item["start_us"]:
            drift_us = max(drift_us, start_us - item["start_us"])

        next_start_us = tts_order[i + 1]["start_us"] if i + 1 < len(tts_order) else total_dur_us_new
        available_us = max(next_start_us - start_us, 1)
        voice_speed = min(max(item["tts_dur_us"] / available_us, 1.0), MAX_VOICE_SPEEDUP)
        speedup_count += voice_speed > 1.0001

        if voice_speed > 1.0:
            seg = cc.AudioSegment(
                item["material"],
                cc.Timerange(start_us, 0),
                source_timerange=cc.Timerange(0, item["tts_dur_us"]),
                speed=voice_speed,
            )
        else:
            seg = cc.AudioSegment(item["material"], cc.Timerange(start_us, item["tts_dur_us"]))
        script.add_segment(seg, track_name=VOICE_TRACK)
        cursor_us = start_us + seg.target_timerange.duration

    for entry in manifest:
        step(f"voice câu {entry['id']}")

    if speedup_count:
        logger.info(
            "assemble {}: vẫn cần tăng tốc thêm {} câu (tối đa {}x) sau khi đã chậm video",
            draft_name,
            speedup_count,
            MAX_VOICE_SPEEDUP,
        )
    if drift_us > 0:
        logger.warning(
            "assemble {}: TTS vẫn trôi tối đa {:.2f}s so với timestamp gốc sau khi đã chậm video + tăng tốc giọng — kiểm lại ở CapCut",
            draft_name,
            drift_us / SEC,
        )

    # --- Phụ đề — mốc thời gian cũng quy đổi qua map_time() để khớp với
    # video đã bị giãn ở những đoạn cần bù giờ.
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
            start_us = max(map_time(sec_to_us(parse_ts(cue.start))), text_cursor_us)
            end_us = map_time(sec_to_us(parse_ts(cue.end)))
            dur_us = max(end_us - start_us, 1)
            clip = cc.ClipSettings(transform_y=_SUBTITLE_TRANSFORM_Y)
            seg = cc.TextSegment(text, cc.Timerange(start_us, dur_us), style=style, clip_settings=clip, border=border)
            script.add_segment(seg, track_name=TEXT_TRACK)
            text_cursor_us = start_us + dur_us
        step(f"phụ đề câu {cue.id}")

    script.save()
    return out_root / draft_name / "draft_content.json"


@dataclass
class EpisodeSource:
    video_path: Path
    background_path: Optional[Path]
    manifest: list[dict]
    vi_cues: list[Cue]
    audio_dir: Path


def assemble_multi(
    sources: list["EpisodeSource"],
    draft_name: str,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    mute_original_audio: bool = False,
    min_video_speed: float = MIN_VIDEO_SPEED,
) -> Path:
    """Ráp NHIỀU tập (episode) nối tiếp nhau vào 1 draft CapCut DUY NHẤT —
    dùng cho dự án dài tập. Mỗi tập được xử lý bằng đúng thuật toán stretch/
    map_time/plan như `assemble_project` (tự chứa hoàn toàn theo từng tập,
    không phụ thuộc trạng thái ngoài), chỉ khác: track/draft được tạo 1 lần
    dùng chung cho cả series, và 3 con trỏ thời gian (video/bg, voice, phụ
    đề) khởi đầu từ `episode_offset_us` — mốc kết thúc của tập trước — thay
    vì luôn bắt đầu lại từ 0, để các tập nối tiếp nhau đúng thứ tự trên cùng
    1 timeline."""
    if not sources:
        raise AssembleError("Chưa có tập nào để ráp")
    for src in sources:
        if not src.video_path.exists():
            raise AssembleError(f"Không thấy video: {src.video_path}")
        if not mute_original_audio and (not src.background_path or not src.background_path.exists()):
            raise AssembleError(f"Không thấy audio nền đã tách: {src.background_path}")

    import pycapcut as cc

    SEC = cc.SEC
    _patch_text_line_max_width(cc.TextSegment)

    def sec_to_us(seconds: float) -> int:
        return _sec_to_us(seconds, SEC)

    out_root = config.capcut_drafts_dir()
    folder = cc.DraftFolder(str(out_root))

    first_video_material = cc.VideoMaterial(str(sources[0].video_path))
    script = folder.create_draft(
        draft_name, first_video_material.width, first_video_material.height, fps=30, allow_replace=True
    )
    script.add_track(cc.TrackType.video, VIDEO_TRACK)
    if not mute_original_audio:
        script.add_track(cc.TrackType.audio, BACKGROUND_TRACK)
    script.add_track(cc.TrackType.audio, VOICE_TRACK)
    script.add_track(cc.TrackType.text, TEXT_TRACK)

    total_steps = sum(3 + len(src.manifest) + len(src.vi_cues) for src in sources)
    done = 0

    def step(label: str) -> None:
        nonlocal done
        done += 1
        if on_progress:
            on_progress(done, total_steps, label)

    style = cc.TextStyle(size=6.0, color=(1.0, 1.0, 1.0), align=1, max_line_width=0.82)
    border = cc.TextBorder(color=(0.0, 0.0, 0.0), width=40.0)

    episode_offset_us = 0
    for ep_index, src in enumerate(sources):
        video_material = first_video_material if ep_index == 0 else cc.VideoMaterial(str(src.video_path))
        bg_material = None if mute_original_audio else cc.AudioMaterial(str(src.background_path))

        tts_windows: dict[int, dict] = {}
        for entry in src.manifest:
            if not entry.get("path"):
                continue
            audio_path = src.audio_dir / entry["path"]
            material = cc.AudioMaterial(str(audio_path))
            wanted_start_us = sec_to_us(parse_ts(entry["start"]))
            wanted_end_us = sec_to_us(parse_ts(entry["end"]))
            window_us = max(wanted_end_us - wanted_start_us, 1)
            tts_dur_us = material.duration

            needed_speed_full = tts_dur_us / window_us
            if needed_speed_full <= 1.0:
                video_speed = 1.0
            else:
                ideal_video_speed = window_us / tts_dur_us
                video_speed = max(ideal_video_speed, min_video_speed)

            tts_windows[entry["id"]] = {
                "material": material,
                "start_us": wanted_start_us,
                "end_us": wanted_end_us,
                "tts_dur_us": tts_dur_us,
                "video_speed": video_speed,
            }

        stretch_intervals: list[tuple[int, int, float]] = []
        for w in sorted(tts_windows.values(), key=lambda w: w["start_us"]):
            if w["video_speed"] < 1.0:
                s = max(w["start_us"], stretch_intervals[-1][1] if stretch_intervals else 0)
                if w["end_us"] > s:
                    stretch_intervals.append((s, w["end_us"], w["video_speed"]))

        def map_time(t_us: int, _stretch_intervals=stretch_intervals) -> int:
            shift = 0
            for s, e, spd in _stretch_intervals:
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

        total_dur_us = video_material.duration
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
        if cursor < total_dur_us:
            plan.append((cursor, total_dur_us - cursor, 1.0))

        timeline_cursor_us = episode_offset_us
        slowdown_count = 0
        for source_start_us, source_dur_us, spd in plan:
            source_dur_us = min(source_dur_us, total_dur_us - source_start_us)
            if source_dur_us <= 0:
                continue

            if spd == 1.0:
                v_seg = cc.VideoSegment(
                    video_material,
                    cc.Timerange(timeline_cursor_us, source_dur_us),
                    source_timerange=cc.Timerange(source_start_us, source_dur_us),
                    volume=0.0,
                )
            else:
                v_seg = cc.VideoSegment(
                    video_material,
                    cc.Timerange(timeline_cursor_us, 0),
                    source_timerange=cc.Timerange(source_start_us, source_dur_us),
                    speed=spd,
                    volume=0.0,
                )
                slowdown_count += 1
            script.add_segment(v_seg, track_name=VIDEO_TRACK)
            new_dur_us = v_seg.target_timerange.duration

            if not mute_original_audio:
                bg_source_dur_us = min(source_dur_us, max(bg_material.duration - source_start_us, 0))
                if bg_source_dur_us > 0:
                    if spd == 1.0:
                        bg_seg = cc.AudioSegment(
                            bg_material,
                            cc.Timerange(timeline_cursor_us, bg_source_dur_us),
                            source_timerange=cc.Timerange(source_start_us, bg_source_dur_us),
                            volume=BACKGROUND_VOLUME,
                        )
                    else:
                        bg_seg = cc.AudioSegment(
                            bg_material,
                            cc.Timerange(timeline_cursor_us, 0),
                            source_timerange=cc.Timerange(source_start_us, bg_source_dur_us),
                            speed=spd,
                            volume=BACKGROUND_VOLUME,
                        )
                    script.add_segment(bg_seg, track_name=BACKGROUND_TRACK)

            timeline_cursor_us += new_dur_us
        total_dur_us_new = timeline_cursor_us
        step(f"video gốc (tập {ep_index + 1})")
        step(f"nhạc nền (tập {ep_index + 1})")

        if slowdown_count:
            logger.info(
                "assemble {} tập {}: chậm video {} đoạn (tối thiểu {}x)",
                draft_name, ep_index + 1, slowdown_count, min_video_speed,
            )

        tts_order = [
            {**tts_windows[entry["id"]], "entry": entry, "start_us": episode_offset_us + map_time(tts_windows[entry["id"]]["start_us"])}
            for entry in src.manifest
            if entry.get("path")
        ]

        cursor_us = episode_offset_us
        drift_us = 0
        speedup_count = 0
        for i, item in enumerate(tts_order):
            start_us = max(item["start_us"], cursor_us)
            if start_us > item["start_us"]:
                drift_us = max(drift_us, start_us - item["start_us"])

            next_start_us = tts_order[i + 1]["start_us"] if i + 1 < len(tts_order) else total_dur_us_new
            available_us = max(next_start_us - start_us, 1)
            voice_speed = min(max(item["tts_dur_us"] / available_us, 1.0), MAX_VOICE_SPEEDUP)
            speedup_count += voice_speed > 1.0001

            if voice_speed > 1.0:
                seg = cc.AudioSegment(
                    item["material"],
                    cc.Timerange(start_us, 0),
                    source_timerange=cc.Timerange(0, item["tts_dur_us"]),
                    speed=voice_speed,
                )
            else:
                seg = cc.AudioSegment(item["material"], cc.Timerange(start_us, item["tts_dur_us"]))
            script.add_segment(seg, track_name=VOICE_TRACK)
            cursor_us = start_us + seg.target_timerange.duration

        for entry in src.manifest:
            step(f"voice câu {entry['id']} (tập {ep_index + 1})")

        if speedup_count:
            logger.info(
                "assemble {} tập {}: vẫn cần tăng tốc thêm {} câu (tối đa {}x)",
                draft_name, ep_index + 1, speedup_count, MAX_VOICE_SPEEDUP,
            )
        if drift_us > 0:
            logger.warning(
                "assemble {} tập {}: TTS vẫn trôi tối đa {:.2f}s so với timestamp gốc",
                draft_name, ep_index + 1, drift_us / SEC,
            )

        text_cursor_us = episode_offset_us
        for cue in src.vi_cues:
            text = (cue.text or "").strip()
            if text:
                start_us = max(episode_offset_us + map_time(sec_to_us(parse_ts(cue.start))), text_cursor_us)
                end_us = episode_offset_us + map_time(sec_to_us(parse_ts(cue.end)))
                dur_us = max(end_us - start_us, 1)
                clip = cc.ClipSettings(transform_y=_SUBTITLE_TRANSFORM_Y)
                seg = cc.TextSegment(text, cc.Timerange(start_us, dur_us), style=style, clip_settings=clip, border=border)
                script.add_segment(seg, track_name=TEXT_TRACK)
                text_cursor_us = start_us + dur_us
            step(f"phụ đề câu {cue.id} (tập {ep_index + 1})")

        episode_offset_us = total_dur_us_new

    script.save()
    return out_root / draft_name / "draft_content.json"
