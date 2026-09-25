from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from .. import config
from ..utils.srt import Cue, parse_ts
from . import timing

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
    background_volume: float = BACKGROUND_VOLUME,
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

    # --- Bước 1+2: giãn video/nhạc nền theo timing dùng chung với export
    # trực tiếp (app/stages/timing.py) — xem docstring ở đó cho công thức
    # đầy đủ, KHÔNG lặp lại logic ở đây nữa. ---
    tts_material_cache: dict[str, "cc.AudioMaterial"] = {}

    def audio_duration_us(path: Path) -> int:
        material = cc.AudioMaterial(str(path))
        tts_material_cache[str(path)] = material
        return material.duration

    total_dur_us = video_material.duration
    plan_result = timing.compute_timeline_plan(
        manifest=manifest,
        vi_cues=vi_cues,
        video_duration_us=total_dur_us,
        audio_dir=audio_dir,
        sec_per_unit=SEC,
        audio_duration_us_fn=audio_duration_us,
        min_video_speed=min_video_speed,
    )
    plan = plan_result.video_plan

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
                        volume=background_volume,
                    )
                else:
                    bg_seg = cc.AudioSegment(
                        bg_material,
                        cc.Timerange(timeline_cursor_us, 0),
                        source_timerange=cc.Timerange(source_start_us, bg_source_dur_us),
                        speed=spd,
                        volume=background_volume,
                    )
                script.add_segment(bg_seg, track_name=BACKGROUND_TRACK)

        timeline_cursor_us += new_dur_us
    step("video gốc")
    step("nhạc nền")

    if slowdown_count:
        logger.info(
            "assemble {}: chậm video {} đoạn (tối thiểu {}x) để giọng đọc có thêm thời gian",
            draft_name,
            slowdown_count,
            min_video_speed,
        )

    # --- Bước 3: giọng đọc TTS — đặt theo `plan_result.voice_placement`/
    # `voice_speed` đã tính sẵn (timing.py, dựa trên mốc ƯỚC TÍNH của cue kề
    # trước/sau). NHƯNG start_us thật sự đưa vào từng AudioSegment vẫn phải
    # kẹp lại theo `cursor_us` THẬT (từ `seg.target_timerange.duration` do
    # CHÍNH pycapcut trả về) — pycapcut làm tròn duration hơi khác công thức
    # ước tính của timing.py (lệch vài chục micro-giây/câu, cộng dồn), nếu
    # tin thẳng mốc ước tính mà không kẹp lại theo cursor thật, 2 segment kề
    # nhau có thể chồng lên nhau (pycapcut báo lỗi cứng SegmentOverlap) —
    # phải giữ đúng kiểu kẹp tuần tự này như bản gốc trước khi tách hàm dùng
    # chung (đã bắt được bằng cách so trực tiếp với 1 draft_content.json cũ
    # thật, xem lịch sử sửa file này).
    speedup_count = 0
    cursor_us = 0
    drift_us = 0
    voice_placement: dict[int, tuple[int, int]] = {}
    for entry in manifest:
        if not entry.get("path") or entry["id"] not in plan_result.voice_placement:
            continue
        cue_id = entry["id"]
        material = tts_material_cache[str(audio_dir / entry["path"])]
        wanted_start_us = plan_result.voice_placement[cue_id][0]
        start_us = max(wanted_start_us, cursor_us)
        if start_us > wanted_start_us:
            drift_us = max(drift_us, start_us - wanted_start_us)
        voice_speed = plan_result.voice_speed[cue_id]
        speedup_count += voice_speed > 1.0001

        if voice_speed > 1.0:
            seg = cc.AudioSegment(
                material,
                cc.Timerange(start_us, 0),
                source_timerange=cc.Timerange(0, material.duration),
                speed=voice_speed,
            )
        else:
            seg = cc.AudioSegment(material, cc.Timerange(start_us, material.duration))
        script.add_segment(seg, track_name=VOICE_TRACK)
        cursor_us = start_us + seg.target_timerange.duration
        voice_placement[cue_id] = (start_us, cursor_us)

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

    # --- Phụ đề — LẤY ĐÚNG mốc giọng đọc thật đã đặt ở voice_placement (Bước
    # 3 ở trên), không tự tính lại từ [cue.start, cue.end] gốc — 2 khung này
    # thường KHÔNG khớp nhau (câu tiếng Việt đọc nhanh/chậm hơn khung gốc của
    # câu tiếng Trung), gây hiện tượng giọng đọc xong rồi mà phụ đề vẫn còn
    # hiện trên CapCut (hoặc ngược lại, phụ đề tắt trước khi đọc xong). Cue
    # không có audio (lỗi/rỗng) mới fallback về map_time(cue.start/end) như cũ.
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
            placement = voice_placement.get(cue.id)
            if placement is not None:
                raw_start_us, raw_end_us = placement
            else:
                raw_start_us = timing.map_time(plan_result.stretch_intervals, sec_to_us(parse_ts(cue.start)))
                raw_end_us = timing.map_time(plan_result.stretch_intervals, sec_to_us(parse_ts(cue.end)))
            start_us = max(raw_start_us, text_cursor_us)
            end_us = max(raw_end_us, start_us)
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
    background_volume: float = BACKGROUND_VOLUME,
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

        tts_material_cache: dict[str, "cc.AudioMaterial"] = {}

        def audio_duration_us(path: Path, _cache=tts_material_cache) -> int:
            material = cc.AudioMaterial(str(path))
            _cache[str(path)] = material
            return material.duration

        total_dur_us = video_material.duration
        plan_result = timing.compute_timeline_plan(
            manifest=src.manifest,
            vi_cues=src.vi_cues,
            video_duration_us=total_dur_us,
            audio_dir=src.audio_dir,
            sec_per_unit=SEC,
            audio_duration_us_fn=audio_duration_us,
            min_video_speed=min_video_speed,
        )
        plan = plan_result.video_plan

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
                            volume=background_volume,
                        )
                    else:
                        bg_seg = cc.AudioSegment(
                            bg_material,
                            cc.Timerange(timeline_cursor_us, 0),
                            source_timerange=cc.Timerange(source_start_us, bg_source_dur_us),
                            speed=spd,
                            volume=background_volume,
                        )
                    script.add_segment(bg_seg, track_name=BACKGROUND_TRACK)

            timeline_cursor_us += new_dur_us
        step(f"video gốc (tập {ep_index + 1})")
        step(f"nhạc nền (tập {ep_index + 1})")

        if slowdown_count:
            logger.info(
                "assemble {} tập {}: chậm video {} đoạn (tối thiểu {}x)",
                draft_name, ep_index + 1, slowdown_count, min_video_speed,
            )

        # Kẹp start_us theo cursor THẬT (không chỉ tin mốc ước tính của
        # timing.py) — xem comment chi tiết trong assemble_project() ở trên,
        # lý do y hệt (đã bắt lỗi thật bằng cách so với 1 draft cũ).
        speedup_count = 0
        cursor_us = episode_offset_us
        drift_us = 0
        # Mốc THẬT SỰ giọng đọc từng câu chiếm — xem comment tương ứng trong
        # assemble_project(). Reset mỗi tập vì cue.id đánh số lại từ đầu.
        voice_placement: dict[int, tuple[int, int]] = {}
        for entry in src.manifest:
            if not entry.get("path") or entry["id"] not in plan_result.voice_placement:
                continue
            cue_id = entry["id"]
            material = tts_material_cache[str(src.audio_dir / entry["path"])]
            wanted_start_us = episode_offset_us + plan_result.voice_placement[cue_id][0]
            start_us = max(wanted_start_us, cursor_us)
            if start_us > wanted_start_us:
                drift_us = max(drift_us, start_us - wanted_start_us)
            voice_speed = plan_result.voice_speed[cue_id]
            speedup_count += voice_speed > 1.0001

            if voice_speed > 1.0:
                seg = cc.AudioSegment(
                    material,
                    cc.Timerange(start_us, 0),
                    source_timerange=cc.Timerange(0, material.duration),
                    speed=voice_speed,
                )
            else:
                seg = cc.AudioSegment(material, cc.Timerange(start_us, material.duration))
            script.add_segment(seg, track_name=VOICE_TRACK)
            cursor_us = start_us + seg.target_timerange.duration
            voice_placement[cue_id] = (start_us, cursor_us)

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
                placement = voice_placement.get(cue.id)
                if placement is not None:
                    raw_start_us, raw_end_us = placement
                else:
                    raw_start_us = episode_offset_us + timing.map_time(plan_result.stretch_intervals, sec_to_us(parse_ts(cue.start)))
                    raw_end_us = episode_offset_us + timing.map_time(plan_result.stretch_intervals, sec_to_us(parse_ts(cue.end)))
                start_us = max(raw_start_us, text_cursor_us)
                end_us = max(raw_end_us, start_us)
                dur_us = max(end_us - start_us, 1)
                clip = cc.ClipSettings(transform_y=_SUBTITLE_TRANSFORM_Y)
                seg = cc.TextSegment(text, cc.Timerange(start_us, dur_us), style=style, clip_settings=clip, border=border)
                script.add_segment(seg, track_name=TEXT_TRACK)
                text_cursor_us = start_us + dur_us
            step(f"phụ đề câu {cue.id} (tập {ep_index + 1})")

        episode_offset_us += plan_result.total_dur_us

    script.save()
    return out_root / draft_name / "draft_content.json"
