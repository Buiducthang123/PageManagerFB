from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from .. import jobs as jobs_mod
from ..utils.srt import Cue, format_ts, write_srt
from . import timing

EXPORT_TIMEOUT_S = 3600.0
# Nhạc nền/SFX gốc đã tách — tái dùng đúng mức trộn CapCut đang dùng
# (assemble.py::BACKGROUND_VOLUME) để 2 đường xuất nghe cân bằng như nhau.
BACKGROUND_VOLUME = 0.8
# Mức âm lượng mặc định cho audio_mode="original" (giữ nguyên âm thanh gốc,
# không tách nhạc nền) — người dùng chốt -13dB làm mặc định hệ thống, chỉnh
# được riêng theo dự án qua field `original_audio_volume_db`.
ORIGINAL_AUDIO_VOLUME_DB = -13.0
MUSIC_VOLUME = 0.35
LOGO_MARGIN_PX = 24
LOGO_WIDTH_PX = 110
# Đã test thật (xem app/stages/transcribe_ocr.py-style feasibility test trên
# clip 25s test4): blur vừa đủ che chữ, không quá gắt.
BLUR_STRENGTH = "18:4"
# FontSize của filter `subtitles` được tính theo hệ toạ độ kịch bản MẶC ĐỊNH
# CỦA LIBASS cho file .srt thuần — 288px chiều cao (xem docstring
# `_marginv_units`) — KHÔNG phải theo chiều cao video thật. Giá trị cũ hardcode
# (20) quy đổi ra ~133px thật trên video dọc 1920px (20 * 1920/288) — quá to,
# gần 1/8 chiều cao khung hình MỖI DÒNG (đã xác nhận thật qua ảnh chụp khung
# hình xuất ra, người dùng phản ánh "rất bất cân xứng"). Giờ cho chỉnh được
# qua `subtitle_font_size` (render_video) — mặc định 6 theo yêu cầu người
# dùng, quy đổi ra ~40px trên video 1920px.
DEFAULT_SUBTITLE_FONT_SIZE = 6


class ExportDirectError(RuntimeError):
    pass


def _run_ffmpeg(args: list[str], job: Optional[jobs_mod.JobState]) -> None:
    """Chạy ffmpeg qua Popen + poll 0.5s — cùng pattern cancel cộng tác với
    `app/stages/video_merge.py::_run_ffmpeg` (copy/adapt tại đây vì hàm gốc
    là private của module đó, không import chéo)."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if job is not None:
        job.process = proc
    try:
        start = time.time()
        stdout = stderr = ""
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if job is not None and job.cancel_event.is_set():
                    proc.kill()
                    proc.communicate()
                    raise jobs_mod.JobCancelled("Đã dừng theo yêu cầu người dùng")
                if time.time() - start > EXPORT_TIMEOUT_S:
                    proc.kill()
                    proc.communicate()
                    raise ExportDirectError(f"ffmpeg chạy quá {EXPORT_TIMEOUT_S:.0f}s — đã huỷ")
        if proc.returncode != 0:
            raise ExportDirectError(f"ffmpeg lỗi: {(stderr or '')[-4000:]}")
    finally:
        if job is not None:
            job.process = None


def _ffprobe_duration_us(path: Path) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return round(float(result.stdout.strip()) * 1_000_000)
    except ValueError as err:
        raise ExportDirectError(f"ffprobe không đọc được thời lượng: {path.name}") from err


def _marginv_units(desired_real_px: float, video_h_px: int) -> int:
    """Quy đổi 1 khoảng cách THẬT (px, tính trên chiều cao video gốc) sang giá
    trị `MarginV` cần truyền cho `force_style` của filter `subtitles`.

    Đã xác nhận thật bằng đo trực tiếp (không đoán): filter `subtitles` khi
    convert 1 file `.srt` THUẦN (không có header ASS/PlayResY) tự ý dùng độ
    phân giải kịch bản MẶC ĐỊNH KINH ĐIỂN CỦA LIBASS — 288px chiều cao — làm
    hệ quy chiếu cho MỌI giá trị margin trong style, HOÀN TOÀN KHÔNG PHỤ
    THUỘC kích thước video thật đang render. Test đo trên video cao 1080px:
    MarginV=34 → cách đáy thật ~142px, MarginV=70 → cách đáy thật ~277px —
    khớp gần như tuyệt đối với hệ số 1080/288=3.75 (không phải 1:1 như trực
    giác), nghĩa là truyền thẳng "muốn cách đáy 70px" → MarginV=70 sẽ ra thật
    ~262px, sai gần gấp 4 lần — đây chính là lý do bản vá MarginV trước đó
    (chỉ cộng trừ vài chục px) không hề thấy phụ đề nhích chút nào so với
    trước khi vá. Đặt `original_size` cho filter KHÔNG sửa được vấn đề này
    (đã thử trực tiếp, kết quả y hệt) — chỉ có cách tự quy đổi ngược ở đây."""
    scale = 288 / video_h_px
    return max(2, round(desired_real_px * scale))


def _ffprobe_wh_px(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        w_str, h_str = result.stdout.strip().splitlines()[0].split(",")
        return int(w_str), int(h_str)
    except (ValueError, IndexError) as err:
        raise ExportDirectError(f"ffprobe không đọc được độ phân giải: {path.name}") from err


# Tỉ lệ cỡ chữ/chiều RỘNG video đã xác nhận thật (xem docstring `_marginv_units`)
# — `subtitle_font_size=6` render trên video DỌC 1080x1920 ra chữ cao ~40px
# thật, 40/1080 ≈ 3.7% chiều rộng, người dùng xác nhận nhìn cân xứng. Cỡ chữ cũ
# (`FontSize` truyền thẳng cho filter `subtitles`) chỉ quy đổi theo CHIỀU CAO
# video (hệ toạ độ kịch bản cố định 288px của libass) — cùng 1 số 6 áp cho
# video NGANG 1920x1080 (chiều cao chỉ 1080, bằng hơn nửa video dọc) ra chữ
# thật chỉ ~22.5px, nhỏ hẳn — không phải vì công thức áp dụng sai cho riêng
# video đó (vẫn đúng % CHIỀU CAO của chính nó), mà vì video ngang nhúng vào
# khung xem dọc (TikTok/feed) hiển thị ra màn hình THẤP hơn hẳn theo chiều cao
# thật so với video dọc cùng chiều rộng — quy đổi theo chiều RỘNG (ổn định hơn
# giữa các tỉ lệ khung hình khi cùng nhúng vào 1 khung xem) mới giữ được cỡ chữ
# nhìn cân xứng dù video dọc hay ngang. Chỉ áp dụng auto này khi
# `subtitle_font_size` còn nguyên giá trị mặc định (`DEFAULT_SUBTITLE_FONT_SIZE`)
# — người dùng đã tự tay chỉnh số khác thì tôn trọng đúng số đó, không tự ý ghi đè.
_REFERENCE_FONT_SIZE_AT_1080W = DEFAULT_SUBTITLE_FONT_SIZE / (1080 / 1920)


def _auto_subtitle_font_size(video_w_px: int, video_h_px: int) -> int:
    if video_h_px <= 0:
        return DEFAULT_SUBTITLE_FONT_SIZE
    return max(2, round(_REFERENCE_FONT_SIZE_AT_1080W * (video_w_px / video_h_px)))


# Số câu thoại trộn chung 1 nhóm — mỗi nhóm là 1 lệnh ffmpeg riêng với chừng
# này đầu vào (80 đường dẫn ~9.000 ký tự, dư xa giới hạn dòng lệnh Windows).
VOICE_CHUNK_SIZE = 80


def _premix_voices(
    specs: list[tuple[Path, float, int]],
    tmp_dir: Path,
    job: Optional[jobs_mod.JobState],
) -> list[tuple[Path, int]]:
    """Trộn trước các câu giọng đọc theo nhóm `VOICE_CHUNK_SIZE` câu thành
    file WAV trung gian. `specs` = (file giọng đọc, hệ số tăng tốc, mốc bắt
    đầu ms trên timeline đầu ra). Mỗi file nhóm bắt đầu tại mốc câu ĐẦU của
    nhóm — nơi gọi `adelay` đúng mốc đó. Kết quả trộn giống hệt trộn chung 1
    lần: các câu không chồng nhau (mốc bắt đầu luôn ≥ lúc câu trước kết thúc)
    và đều `normalize=0`, nên cộng theo nhóm hay cộng 1 lần là như nhau.
    Trả [(file nhóm, mốc bắt đầu ms)]."""
    chunks: list[tuple[Path, int]] = []
    for k in range(0, len(specs), VOICE_CHUNK_SIZE):
        if job is not None:
            job.raise_if_cancelled()
        group = specs[k : k + VOICE_CHUNK_SIZE]
        base_ms = group[0][2]
        args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        parts: list[str] = []
        labels: list[str] = []
        for j, (path, speed, start_ms) in enumerate(group):
            args += ["-i", str(path)]
            tempo = f"atempo={speed}," if speed > 1.0 else ""
            parts.append(f"[{j}:a]{tempo}adelay={start_ms - base_ms}:all=1[v{j}]")
            labels.append(f"[v{j}]")
        if len(labels) == 1:
            parts.append(f"{labels[0]}anull[out]")
        else:
            parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0[out]")
        n = k // VOICE_CHUNK_SIZE
        script = tmp_dir / f"voices_{n}.txt"
        script.write_text(";".join(parts), encoding="utf-8")
        out = tmp_dir / f"voices_{n}.wav"
        args += ["-/filter_complex", str(script), "-map", "[out]", "-c:a", "pcm_s16le", str(out)]
        _run_ffmpeg(args, job)
        chunks.append((out, base_ms))
    return chunks


def render_video(
    video_path: Path,
    background_path: Optional[Path],
    manifest: list[dict],
    vi_cues: list[Cue],
    audio_dir: Path,
    output_path: Path,
    blur_region: Optional[tuple[float, float, float, float]] = None,
    # Mốc [start_s, end_s] (giây, THEO TIMELINE VIDEO GỐC — vd lấy thẳng từ
    # sub_zh.srt) mà phụ đề cứng THẬT SỰ hiện trên khung hình — có giá trị
    # thì chỉ che ĐÚNG những đoạn này (tự quy đổi sang timeline đầu ra đã
    # giãn/chậm theo giọng đọc bên dưới), None = che SUỐT video như cũ (không
    # có dữ liệu thời điểm, vd engine transcribe không phải OCR).
    blur_active_ranges_s: Optional[list[tuple[float, float]]] = None,
    music_path: Optional[Path] = None,
    logo_path: Optional[Path] = None,
    min_video_speed: float = timing.MIN_VIDEO_SPEED,
    background_volume_db: Optional[float] = None,
    music_volume_db: Optional[float] = None,
    subtitle_font_size: int = DEFAULT_SUBTITLE_FONT_SIZE,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    job: Optional[jobs_mod.JobState] = None,
) -> Path:
    if not video_path.exists():
        raise ExportDirectError(f"Không thấy video: {video_path}")

    def progress(done: int, total: int, label: str) -> None:
        if job is not None:
            job.raise_if_cancelled()
        if on_progress:
            on_progress(done, total, label)

    progress(0, 5, "tính timeline")
    video_duration_us = _ffprobe_duration_us(video_path)
    plan_result = timing.compute_timeline_plan(
        manifest=manifest,
        vi_cues=vi_cues,
        video_duration_us=video_duration_us,
        audio_dir=audio_dir,
        sec_per_unit=1_000_000,
        audio_duration_us_fn=_ffprobe_duration_us,
        min_video_speed=min_video_speed,
    )
    total_dur_s = plan_result.total_dur_us / 1_000_000

    # --- Bước 1: video + nhạc nền — N đoạn trim/setpts (video) và
    # atrim/atempo (nhạc nền, CÙNG hệ số speed để khớp nhịp) rồi concat. ---
    has_bg = background_path is not None and background_path.exists()
    inputs: list[str] = ["-i", str(video_path)]
    idx_video = 0
    idx_bg: Optional[int] = None
    if has_bg:
        inputs += ["-i", str(background_path)]
        idx_bg = 1

    filter_parts: list[str] = []
    sv_labels: list[str] = []
    sa_labels: list[str] = []
    for i, (src_start_us, src_dur_us, spd) in enumerate(plan_result.video_plan):
        src_start_s = src_start_us / 1_000_000
        src_dur_s = src_dur_us / 1_000_000
        vlabel = f"sv{i}"
        vpts = "PTS-STARTPTS" if spd == 1.0 else f"(PTS-STARTPTS)/{spd}"
        filter_parts.append(f"[{idx_video}:v]trim=start={src_start_s:.6f}:duration={src_dur_s:.6f},setpts={vpts}[{vlabel}]")
        sv_labels.append(vlabel)
        if has_bg:
            alabel = f"sa{i}"
            atempo = "" if spd == 1.0 else f",atempo={spd}"
            filter_parts.append(
                f"[{idx_bg}:a]atrim=start={src_start_s:.6f}:duration={src_dur_s:.6f},asetpts=PTS-STARTPTS{atempo}[{alabel}]"
            )
            sa_labels.append(alabel)

    if has_bg:
        concat_inputs = "".join(f"[{v}][{a}]" for v, a in zip(sv_labels, sa_labels))
        filter_parts.append(f"{concat_inputs}concat=n={len(sv_labels)}:v=1:a=1[vfull][bgfull]")
    else:
        concat_inputs = "".join(f"[{v}]" for v in sv_labels)
        filter_parts.append(f"{concat_inputs}concat=n={len(sv_labels)}:v=1:a=0[vfull]")
    progress(1, 5, "dựng video + nhạc nền")

    # --- Bước 2: che vùng phụ đề cũ (nếu có khoanh vùng) ---
    v_label = "vfull"
    video_w_px, video_h_px = _ffprobe_wh_px(video_path)
    if subtitle_font_size == DEFAULT_SUBTITLE_FONT_SIZE:
        subtitle_font_size = _auto_subtitle_font_size(video_w_px, video_h_px)
    # MarginV mặc định (không khoanh vùng che) — đổi từ hằng số cũ (36) sang
    # gọi `_marginv_units` để cùng đi qua phép quy đổi tỉ lệ bên dưới, tránh
    # lặp lại đúng bug margin cũ (xem docstring `_marginv_units`).
    margin_v_units = _marginv_units(36, video_h_px)
    if blur_region:
        x, y, w, h = blur_region
        filter_parts.append(f"[{v_label}]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},boxblur={BLUR_STRENGTH}[blurred]")
        overlay_enable = ""
        if blur_active_ranges_s:
            # Quy đổi mốc thời gian THEO VIDEO GỐC (vd lấy từ sub_zh.srt, lúc
            # chữ thật sự hiện trên khung hình) sang timeline ĐẦU RA đã giãn/
            # chậm theo giọng đọc (cùng phép quy đổi burn phụ đề mới bên
            # dưới dùng) — chỉ bật blur ĐÚNG những đoạn này thay vì che suốt
            # video, theo yêu cầu người dùng.
            windows = []
            for s_s, e_s in blur_active_ranges_s:
                out_s = timing.map_time(plan_result.stretch_intervals, round(s_s * 1_000_000)) / 1_000_000
                out_e = timing.map_time(plan_result.stretch_intervals, round(e_s * 1_000_000)) / 1_000_000
                if out_e > out_s:
                    windows.append(f"between(t,{out_s:.3f},{out_e:.3f})")
            if windows:
                overlay_enable = f":enable='{'+'.join(windows)}'"
        filter_parts.append(f"[{v_label}][blurred]overlay=W*{x}:H*{y}{overlay_enable}[vblur]")
        v_label = "vblur"
        # Phụ đề mới phải NẰM TRONG vùng che (thay chữ cũ), không phải giữ
        # margin cố định tính từ đáy khung hình — đã xác nhận thật: vùng che
        # tự động phát hiện hiếm khi khớp đúng vị trí margin cố định, làm phụ
        # đề mới nổi lên TRÊN dải che thay vì nằm đè lên nó.
        #
        # Căn GIỮA vùng che theo chiều dọc (không neo theo đáy) — đã xác nhận
        # thật bằng cách trích khung hình xuất ra rồi đo pixel: neo theo đáy
        # (bản cũ) làm chữ dồn sát mép dưới dải che, lệch hẳn khỏi tâm, nhìn
        # mất cân đối so với dải che. ASS Alignment=2 neo MÉP DƯỚI của chữ
        # cách đáy khung hình 1 khoảng (MarginV) — để tâm dòng chữ trùng tâm
        # dải che, margin đó phải trừ thêm nửa chiều cao dòng chữ. `subtitle_font_size`
        # được khai theo hệ toạ độ kịch bản 288px (xem `_marginv_units`), quy
        # đổi ra chiều cao dòng chữ THẬT theo hệ số dòng ~1.2x chuẩn ASS/libass.
        LINE_HEIGHT_FACTOR = 1.2
        text_line_height_px = subtitle_font_size * LINE_HEIGHT_FACTOR * video_h_px / 288
        box_center_frac = y + h / 2
        desired_gap_px = (1 - box_center_frac) * video_h_px - text_line_height_px / 2
        margin_v_units = _marginv_units(desired_gap_px, video_h_px)

    # --- Bước 3: burn phụ đề mới — mốc LẤY TỪ voice_placement (giống hệt
    # assemble.py, không dùng lại [cue.start, cue.end] gốc). ---
    tmp_dir = Path(tempfile.mkdtemp(prefix="export_direct_"))
    srt_path = tmp_dir / "burn.srt"
    burn_cues: list[Cue] = []
    text_cursor_us = 0
    for cue in vi_cues:
        text = (cue.text or "").strip()
        if not text:
            continue
        placement = plan_result.voice_placement.get(cue.id)
        if placement is not None:
            raw_start_us, raw_end_us = placement
        else:
            from ..utils.srt import parse_ts

            raw_start_us = timing.map_time(plan_result.stretch_intervals, round(parse_ts(cue.start) * 1_000_000))
            raw_end_us = timing.map_time(plan_result.stretch_intervals, round(parse_ts(cue.end) * 1_000_000))
        start_us = max(raw_start_us, text_cursor_us)
        end_us = max(raw_end_us, start_us + 1)
        burn_cues.append(Cue(id=len(burn_cues) + 1, start=format_ts(start_us / 1_000_000), end=format_ts(end_us / 1_000_000), text=text))
        text_cursor_us = end_us
    write_srt(srt_path, burn_cues)

    # Đường dẫn Windows (ổ đĩa `C:\...`) đụng dấu `:` — ký tự phân tách option
    # của filter `subtitles` — phải escape kép (`\\:`) rồi BỌC NGOẶC ĐƠN cả
    # chuỗi filename mới parse đúng (đã xác nhận qua lỗi thật: escape dấu `:`
    # không thôi vẫn làm ffmpeg đọc lệch sang option `original_size`).
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
    style = f"FontName=Arial,FontSize={subtitle_font_size},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV={margin_v_units}"
    filter_parts.append(f"[{v_label}]subtitles='{srt_escaped}':force_style='{style}'[vsub]")
    v_label = "vsub"
    progress(2, 5, "burn phụ đề mới")

    # --- Bước 4: logo (nếu có) ---
    has_logo = logo_path is not None and logo_path.exists()
    idx_logo: Optional[int] = None
    if has_logo:
        idx_logo = len(inputs) // 2
        inputs += ["-i", str(logo_path)]
        filter_parts.append(f"[{idx_logo}:v]scale={LOGO_WIDTH_PX}:-1[logo]")
        filter_parts.append(f"[{v_label}][logo]overlay=W-w-{LOGO_MARGIN_PX}:{LOGO_MARGIN_PX}[vout]")
        v_label = "vout"
    else:
        filter_parts.append(f"[{v_label}]null[vout]")
        v_label = "vout"
    progress(3, 5, "chèn logo")

    # --- Bước 5: giọng đọc TTS đặt đúng mốc + trộn với nhạc nền/nhạc ngoài ---
    # Trộn TRƯỚC giọng đọc theo từng nhóm (`_premix_voices`) rồi mới đưa vài
    # file nhóm vào lệnh cuối — trước đây MỖI câu là 1 `-i` riêng: video dài
    # nhiều câu làm dòng lệnh vượt giới hạn 32.767 ký tự của Windows
    # ("[WinError 206] The filename or extension is too long", đã gặp thật với
    # 147 đầu vào + 442 bộ lọc), và video 3 tiếng (~5800 câu) còn mở hàng
    # nghìn file cùng lúc.
    voice_specs: list[tuple[Path, float, int]] = []
    cursor_us = 0
    for entry in manifest:
        if not entry.get("path") or entry["id"] not in plan_result.voice_placement:
            continue
        cue_id = entry["id"]
        audio_path = audio_dir / entry["path"]
        wanted_start_us = plan_result.voice_placement[cue_id][0]
        start_us = max(wanted_start_us, cursor_us)
        speed = plan_result.voice_speed[cue_id]
        tts_dur_us = plan_result.tts_duration_us[cue_id]
        dur_us = round(tts_dur_us / speed) if speed > 1.0 else tts_dur_us
        cursor_us = start_us + dur_us
        voice_specs.append((audio_path, speed, round(start_us / 1000)))

    progress(4, 5, "trộn giọng đọc")
    voice_labels: list[str] = []
    try:
        voice_chunks = _premix_voices(voice_specs, tmp_dir, job)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    for c, (chunk_path, chunk_start_ms) in enumerate(voice_chunks):
        vi_idx = len(inputs) // 2
        inputs += ["-i", str(chunk_path)]
        vlabel = f"voicechunk{c}"
        filter_parts.append(f"[{vi_idx}:a]adelay={chunk_start_ms}:all=1[{vlabel}]")
        voice_labels.append(vlabel)

    has_music = music_path is not None and music_path.exists()
    idx_music: Optional[int] = None
    if has_music:
        idx_music = len(inputs) // 2
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]
        music_volume_expr = f"{music_volume_db}dB" if music_volume_db is not None else str(MUSIC_VOLUME)
        filter_parts.append(f"[{idx_music}:a]volume={music_volume_expr}[music]")

    audio_mix_labels: list[str] = []
    if has_bg:
        # `background_volume_db` (vd -13dB cho audio_mode="original") ghi đè
        # hằng số tuyến tính mặc định — cho phép chỉnh riêng theo từng chế độ
        # âm thanh nền thay vì 1 mức cố định chung cho mọi trường hợp.
        bg_volume_expr = f"{background_volume_db}dB" if background_volume_db is not None else str(BACKGROUND_VOLUME)
        filter_parts.append(f"[bgfull]volume={bg_volume_expr}[bgvol]")
        audio_mix_labels.append("bgvol")
    if has_music:
        audio_mix_labels.append("music")
    audio_mix_labels.extend(voice_labels)

    if audio_mix_labels:
        mix_inputs = "".join(f"[{lb}]" for lb in audio_mix_labels)
        # normalize=0 — tự set volume từng nguồn (giống CapCut), không để
        # ffmpeg tự cân bằng — nhưng vẫn cần 1 limiter an toàn chặn vỡ tiếng
        # khi nhạc nền + giọng đọc cùng lúc to (đã đo thật: max_volume chạm
        # 0dB không có limiter).
        filter_parts.append(
            f"{mix_inputs}amix=inputs={len(audio_mix_labels)}:duration=longest:normalize=0,alimiter=limit=0.95[aout]"
        )
        has_audio_out = True
    else:
        has_audio_out = False
    progress(4, 5, "trộn giọng đọc + nhạc nền")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Bộ lọc ghi ra FILE (`-/filter_complex <file>`, cú pháp ffmpeg ≥ 7.1 thay
    # cho `-filter_complex_script` đã bỏ) thay vì viết thẳng trên dòng lệnh —
    # mỗi đoạn video giãn/chậm là 1-2 bộ lọc, video dài có hàng trăm đoạn.
    graph_path = tmp_dir / "graph.txt"
    graph_path.write_text(";".join(filter_parts), encoding="utf-8")

    args = ["ffmpeg", "-y", *inputs, "-/filter_complex", str(graph_path), "-map", f"[{v_label}]"]
    if has_audio_out:
        args += ["-map", "[aout]"]
    args += ["-t", f"{total_dur_s:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    if has_audio_out:
        args += ["-c:a", "aac"]
    args += [str(output_path)]

    logger.info(
        "export_direct: ffmpeg {} input(s), {} filter(s), {} câu thoại trộn trong {} nhóm",
        len(inputs) // 2, len(filter_parts), len(voice_specs), len(voice_labels),
    )
    try:
        _run_ffmpeg(args, job)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    progress(5, 5, "xong")
    return output_path
