from __future__ import annotations

import json
import random
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger

from . import config, downloads as dl, jobs, merges as mg, projects as pj, settings as app_settings, social as sp
from .models import Episode, QueueItem, QueueItemStatus, SocialLink, SocialProjectSummary, StageRecord, StageStatus
from .schemas import (
    AppSettingsResponse,
    CreateDownloadRequest,
    CreateEpisodeRequest,
    CrawlSocialRequest,
    CreateProjectRequest,
    CreateSocialProjectRequest,
    EpisodeDetail,
    IngestUrlRequest,
    JobItemResponse,
    JobStatusResponse,
    LogEntry,
    ProjectDetailResponse,
    ProjectSummary,
    RenameProjectRequest,
    ReorderEpisodesRequest,
    SplitProjectRequest,
    SrtCue,
    StartAssembleRequest,
    StartExportRequest,
    StartTranscribeRequest,
    StartTTSRequest,
    TTSCueRequest,
    TTSManifestEntryResponse,
    UpdateAppSettingsRequest,
    UpdateAutoPipelineRequest,
    UpdateCueRequest,
    UpdateExportBlurRegionRequest,
    UpdateOcrCropRegionRequest,
    UpdateSocialProjectRequest,
)
from . import social_cleanup
from .stages import assemble as assemble_stage
from .stages import douyin_browser as douyin_browser_stage
from .stages import douyin_dl as douyin_dl_stage
from .stages import dub_audio as dub_audio_stage
from .stages import export_direct as export_direct_stage
from .stages import fetch_url as fetch_url_stage
from .stages import ingest as ingest_stage
from .stages import social_publish as social_publish_stage
from .stages import transcribe as transcribe_stage
from .stages import transcribe_ocr as ocr_stage
from .stages import transcribe_sensevoice as sensevoice_stage
from .stages import translate as translate_stage
from .stages import tts as tts_stage
from .stages import tts_vieneu as tts_vieneu_stage
from .stages import video_merge as video_merge_stage
from .stages import video_split as video_split_stage
from .utils.srt import load_srt, update_cue_text

load_dotenv()

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

app = FastAPI(title="ReupVideoVjpPro")


def _summary(state) -> ProjectSummary:
    return ProjectSummary(
        project_id=state.project_id,
        title=state.title,
        created_at=state.created_at.isoformat(),
        current_stage=pj.current_stage(state),
        project_type=state.project_type,
    )


def _tts_module(engine: str):
    return tts_vieneu_stage if engine == "vieneu" else tts_stage


def _tts_voices(engine: str) -> list[dict[str, str]]:
    return tts_vieneu_stage.VOICES if engine == "vieneu" else tts_stage.VOICES


def _tts_engine_label(engine: str) -> str:
    return "VieNeu-TTS" if engine == "vieneu" else "CapCut TTS"


def _require_done(state, stage: str, message: str) -> None:
    rec = state.stages.get(stage)
    if rec is None or rec.status != StageStatus.done:
        raise HTTPException(status_code=409, detail=message)


def _require_episode_done(episode: Episode, stage: str, message: str) -> None:
    rec = episode.stages.get(stage)
    if rec is None or rec.status != StageStatus.done:
        raise HTTPException(status_code=409, detail=message)


EPISODE_STAGES = ("ingest", "transcribe", "translate", "tts", "assemble")

# 3 engine transcribe — tra cứu 1 chỗ duy nhất thay vì ternary lặp lại ở mỗi
# route/hàm start (project-level, episode-level, auto-pipeline validate).
TRANSCRIBE_ENGINES: dict[str, tuple[object, str]] = {
    "whisper": (transcribe_stage.transcribe_video, "Whisper zh"),
    "sensevoice": (sensevoice_stage.transcribe_video, "SenseVoice"),
    "ocr": (ocr_stage.transcribe_video, "OCR (phụ đề cứng)"),
}
DEFAULT_TRANSCRIBE_ENGINE = "ocr"


def _resolve_transcribe_engine(engine: str | None) -> tuple[str, object, str]:
    key = engine if engine in TRANSCRIBE_ENGINES else DEFAULT_TRANSCRIBE_ENGINE
    stage_fn, label = TRANSCRIBE_ENGINES[key]
    return key, stage_fn, label


def _episode_job_key(project_id: str, episode_id: str, stage: str) -> str:
    return f"{project_id}:{episode_id}:{stage}"


def _episode_busy(project_id: str, episode_id: str) -> bool:
    return any(jobs.is_job_running(_episode_job_key(project_id, episode_id, s)) for s in EPISODE_STAGES)


def _get_episode_or_404(state, episode_id: str) -> Episode:
    try:
        return pj.find_episode(state, episode_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


def _mark_cancelled(project_id: str, stage: str, job: jobs.JobState) -> None:
    job.status = "cancelled"
    for it in job.items:
        if it.status == "running":
            it.status = "failed"
            it.error = "Đã dừng"
    with pj.locked_project(project_id) as s:
        s.stages[stage].status = StageStatus.failed
        s.stages[stage].error = "Đã dừng theo yêu cầu người dùng"
    pj.append_log(project_id, stage, "Đã dừng theo yêu cầu người dùng")
    _maybe_fail_social(project_id, stage, "Đã dừng theo yêu cầu người dùng")


def _maybe_chain(project_id: str, finished_stage: str) -> None:
    """Nếu project bật auto_pipeline, tự động chạy tiếp stage kế tiếp ngay
    sau khi `finished_stage` xong — cho phép "upload xong là chạy tới cùng"
    mà không cần bấm từng nút. Dừng lại sau assemble vì review CapCut + export
    là thao tác tay, chưa có API tương ứng."""
    try:
        state = pj.load_project(project_id)
    except FileNotFoundError:
        return
    if not state.auto_pipeline:
        return
    if finished_stage == "ingest":
        _start_transcribe(project_id, state.auto_engine, crop_region=state.auto_ocr_crop_region)
        _start_early_blur_detect(project_id)
    elif finished_stage == "transcribe":
        _start_translate(project_id)
    elif finished_stage == "translate":
        _start_tts(project_id, state.auto_voice, engine=state.auto_tts_engine)
    elif finished_stage == "tts":
        if state.social_link is not None:
            # Project do "Dự án tự động" sinh ra — bỏ hẳn bước ráp draft
            # CapCut (không ai ngồi mở/duyệt draft đó cả) — xuất trực tiếp
            # bằng ffmpeg (`export_direct.py`) chỉ cần audio/manifest.json +
            # sub_vi.srt, HOÀN TOÀN không phụ thuộc draft CapCut, nên bỏ qua
            # bước này không mất dữ liệu gì, chỉ đỡ tốn thời gian dựng draft
            # vô ích. Project tạo TAY vẫn dừng ở assemble như cũ (dưới đây).
            _start_export(
                project_id,
                audio_mode=state.auto_audio_mode,
                min_video_speed=state.auto_min_video_speed,
                original_audio_volume_db=state.auto_original_audio_volume_db,
                subtitle_font_size=state.auto_subtitle_font_size,
            )
        else:
            _start_assemble(
                project_id,
                audio_mode=state.auto_audio_mode,
                min_video_speed=state.auto_min_video_speed,
                original_audio_volume_db=state.auto_original_audio_volume_db,
            )


def _maybe_chain_social(project_id: str, finished_stage: str) -> None:
    """Bản dành riêng cho project do "Dự án tự động" (app/social.py) sinh ra
    (`state.social_link` có giá trị) — khác `_maybe_chain` ở chỗ CHẠY TIẾP QUA
    EXPORT LUÔN thay vì dừng ở assemble (tự động thì không có người ngồi duyệt
    CapCut), và khi export xong thì báo ngược lại đúng hàng đợi social tương
    ứng để đánh dấu video đó `ready`. Không đụng `_maybe_chain` — project tạo
    tay vẫn dừng ở assemble như cũ."""
    try:
        state = pj.load_project(project_id)
    except FileNotFoundError:
        return
    link = state.social_link
    if link is None:
        return
    if finished_stage == "assemble":
        _start_export(
            project_id,
            audio_mode=state.auto_audio_mode,
            min_video_speed=state.auto_min_video_speed,
            original_audio_volume_db=state.auto_original_audio_volume_db,
            subtitle_font_size=state.auto_subtitle_font_size,
        )
    elif finished_stage == "export":
        try:
            with sp.locked_state(link.social_id) as social_state:
                for item in social_state.queue:
                    if item.aweme_id == link.aweme_id:
                        item.status = QueueItemStatus.ready
                        break
        except FileNotFoundError:
            logger.warning("_maybe_chain_social: không thấy dự án tự động '{}' cho project '{}'", link.social_id, project_id)
        social_cleanup.remove_wav_after_export(project_id)


def _maybe_fail_social(project_id: str, stage: str, error: str) -> None:
    """Báo ngược 1 stage THẤT BẠI (kể cả do bị huỷ/cancel) về đúng hàng đợi
    "Dự án tự động" tương ứng — nếu không có, `QueueItem.status` kẹt mãi ở
    `processing` không lối thoát (đã xác nhận thật: video kẹt vô thời hạn
    sau khi transcribe bị huỷ giữa chừng, không có nút nào để thử lại vì
    frontend chỉ hiện nút retry cho status `failed`). Gọi song song với MỌI
    điểm đánh dấu 1 stage của project đơn thất bại (transcribe/translate/
    tts/assemble/export) — no-op êm nếu project không phải do "Dự án tự
    động" sinh ra (`social_link=None`) hoặc queue item đã ở trạng thái cuối
    (posted/skipped — không ghi đè lại)."""
    try:
        state = pj.load_project(project_id)
    except FileNotFoundError:
        return
    link = state.social_link
    if link is None:
        return
    try:
        with sp.locked_state(link.social_id) as social_state:
            for item in social_state.queue:
                if item.aweme_id == link.aweme_id:
                    if item.status in (QueueItemStatus.posted, QueueItemStatus.skipped):
                        return
                    item.status = QueueItemStatus.failed
                    item.error = f"[{stage}] {error}"
                    item.failed_stage = "activate"
                    break
    except FileNotFoundError:
        logger.warning("_maybe_fail_social: không thấy dự án tự động '{}' cho project '{}'", link.social_id, project_id)


def _detect_blur_region_and_ranges(
    video_path: Path, search_region: tuple[float, float, float, float] | None = None
) -> tuple[tuple[float, float, float, float] | None, list[tuple[float, float]]]:
    """Dò vùng che (`detect_subtitle_region`) rồi, nếu tìm được, dò tiếp các
    khoảng THỜI GIAN chữ thật sự hiện trong vùng đó (`detect_subtitle_visibility`)
    — gộp 2 lượt OCR liên quan thành 1 hàm dùng chung cho cả lượt dò SỚM
    (`_start_early_blur_detect`, chạy nền ngay sau ingest) lẫn lượt dò DỰ
    PHÒNG lúc export (nếu lượt sớm chưa kịp xong/chưa từng chạy). Không raise
    — lỗi ở bước nào cũng chỉ log rồi trả về phần đã có (region rỗng → cả 2
    đều rỗng; visibility lỗi → vẫn trả region, ranges rỗng nghĩa là "che suốt
    video" ở nơi gọi)."""
    try:
        region = ocr_stage.detect_subtitle_region(video_path, search_region=search_region)
    except ocr_stage.TranscribeOCRError as err:
        logger.warning("_detect_blur_region_and_ranges: dò vùng che thất bại cho '{}': {}", video_path, err)
        return None, []
    if region is None:
        return None, []
    try:
        ranges = ocr_stage.detect_subtitle_visibility(video_path, region)
    except ocr_stage.TranscribeOCRError as err:
        logger.warning("_detect_blur_region_and_ranges: dò thời điểm hiện chữ thất bại cho '{}': {}", video_path, err)
        ranges = []
    return region, ranges


def _start_early_blur_detect(project_id: str) -> None:
    """Dò vùng che + thời điểm hiện chữ bằng OCR NGAY SAU KHI TẢI XONG video
    — chạy nền song song với transcribe/translate/tts (chỉ cần file video
    gốc, không phụ thuộc bất kỳ output nào của các bước đó) thay vì đợi tới
    tận bước export mới chạy như trước — tiết kiệm thời gian chờ oan cho
    auto_pipeline, và nếu translate/TTS lỗi giữa chừng thì công dò đã làm
    không bị phí (project fail sớm vẫn có sẵn để export tay lại về sau).
    Chạy nền hoàn toàn (không phải `jobs` — không cần theo dõi tiến trình/
    huỷ, chỉ là tối ưu ẩn) — lỗi thì bỏ qua êm, `_start_export` vẫn tự dò lại
    bình thường nếu tới lúc đó `export_blur_region` còn trống."""

    def run() -> None:
        try:
            state = pj.load_project(project_id)
        except FileNotFoundError:
            return
        if state.export_blur_region or not state.video_relpath:
            return
        video_path = pj.project_dir(project_id) / state.video_relpath
        search_region = tuple(state.auto_ocr_crop_region) if state.auto_ocr_crop_region else None
        region, ranges = _detect_blur_region_and_ranges(video_path, search_region=search_region)
        if region is None:
            return
        try:
            with pj.locked_project(project_id) as s:
                # Kiểm tra lại LẦN NỮA trong lock — tránh ghi đè nếu giữa
                # chừng người dùng đã tự khoanh tay hoặc export đã tự dò
                # xong trước (race hiếm nhưng không phải không thể).
                if not s.export_blur_region:
                    s.export_blur_region = list(region)
                    s.export_blur_active_ranges = [list(r) for r in ranges] if ranges else None
        except FileNotFoundError:
            pass

    threading.Thread(target=run, daemon=True).start()


def _maybe_chain_episode(project_id: str, episode_id: str, finished_stage: str) -> None:
    """Bản dài-tập của `_maybe_chain`: tự chạy tiếp stage kế của CHÍNH tập vừa
    xong. Khi 1 tập xong `tts`, nếu MỌI tập trong dự án đã `tts` xong thì mới
    tự kích assemble chung (ráp cả series) — thêm 1 tập mới vào dự án đã chạy
    xong chỉ khiến đúng tập đó tự chạy lại rồi ráp lại toàn bộ, không đụng
    audio/bản dịch của các tập khác."""
    try:
        state = pj.load_project(project_id)
    except FileNotFoundError:
        return
    if not state.auto_pipeline:
        return
    if finished_stage == "ingest":
        _start_episode_transcribe(project_id, episode_id, state.auto_engine)
    elif finished_stage == "transcribe":
        _start_episode_translate(project_id, episode_id)
    elif finished_stage == "translate":
        _start_episode_tts(project_id, episode_id, state.auto_voice, engine=state.auto_tts_engine)
    elif finished_stage == "tts":
        if state.split_mode:
            # Dự án "split": mỗi đoạn tự ráp draft RIÊNG ngay khi xong TTS,
            # không chờ đoạn khác (khác hẳn "multi" ráp chung 1 draft).
            _start_episode_assemble(
                project_id,
                episode_id,
                audio_mode=state.auto_audio_mode,
                min_video_speed=state.auto_min_video_speed,
                original_audio_volume_db=state.auto_original_audio_volume_db,
            )
        elif pj.all_episodes_stage_done(state, "tts"):
            _start_assemble(
                project_id,
                audio_mode=state.auto_audio_mode,
                min_video_speed=state.auto_min_video_speed,
                original_audio_volume_db=state.auto_original_audio_volume_db,
            )
    elif finished_stage == "assemble" and state.split_mode:
        # Đoạn này ráp draft xong — chỉ giờ mới bắt đầu đoạn KẾ TIẾP (tuần tự,
        # không chạy song song như các tập của dự án "multi").
        #
        # Chỉ tự chạy tập kế tiếp nếu nó THẬT SỰ chưa từng đụng tới
        # (transcribe còn "pending") — nếu không, chạy lại 1 tập bất kỳ ở
        # giữa chuỗi (vd sửa lỗi, dịch lại) sẽ khiến tập SAU nó (dù đã xong
        # từ trước) bị tự động kích hoạt chạy lại theo, chồng lấn job với các
        # tập khác đang chạy — đã xác nhận trực tiếp qua log thật: chạy lại
        # ep-001 xong assemble → ep-002 (đã xong từ hôm trước) tự transcribe
        # lại, trùng lúc ep-003/ep-006 cũng đang chạy.
        episodes = sorted(state.episodes, key=lambda e: e.order)
        current = pj.find_episode(state, episode_id)
        next_ep = next((e for e in episodes if e.order == current.order + 1), None)
        if next_ep is not None and next_ep.stages["transcribe"].status == StageStatus.pending:
            _start_episode_transcribe(project_id, next_ep.episode_id, state.auto_engine)


# ------------------------------------------------------------------ Settings


@app.get("/api/settings", response_model=AppSettingsResponse)
def get_settings_route():
    return app_settings.get_settings()


@app.put("/api/settings", response_model=AppSettingsResponse)
def update_settings_route(body: UpdateAppSettingsRequest):
    try:
        return app_settings.update_settings(
            workspace_dir=body.workspace_dir,
            gemini_api_key=body.gemini_api_key,
            gemini_model=body.gemini_model,
            whisper_model=body.whisper_model,
            whisper_device=body.whisper_device,
            whisper_language=body.whisper_language,
            translate_pace=body.translate_pace,
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


# ------------------------------------------------------------------ Projects


@app.get("/api/projects", response_model=list[ProjectSummary])
def list_projects_route():
    return [_summary(s) for s in pj.list_projects()]


@app.post("/api/projects", response_model=ProjectSummary, status_code=201)
def create_project_route(body: CreateProjectRequest):
    try:
        state = pj.create_project(body.title, body.project_type)
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return _summary(state)


@app.patch("/api/projects/{project_id}", response_model=ProjectSummary)
def rename_project_route(project_id: str, body: RenameProjectRequest):
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Tên dự án không được để trống")
    try:
        state = pj.rename_project(project_id, body.title.strip())
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return _summary(state)


@app.patch("/api/projects/{project_id}/auto-pipeline", response_model=ProjectSummary)
def update_auto_pipeline_route(project_id: str, body: UpdateAutoPipelineRequest):
    engine, _, _ = _resolve_transcribe_engine(body.engine)
    tts_engine = "vieneu" if body.tts_engine == "vieneu" else "capcut"
    audio_mode = body.audio_mode if body.audio_mode in ("separated", "original", "mute") else "original"
    with pj.locked_project(project_id) as state:
        state.auto_pipeline = body.enabled
        state.auto_engine = engine
        state.auto_tts_engine = tts_engine
        state.auto_voice = body.voice if body.voice in {v["id"] for v in _tts_voices(tts_engine)} else ""
        state.auto_audio_mode = audio_mode
        state.auto_original_audio_volume_db = body.original_audio_volume_db
        state.auto_min_video_speed = body.min_video_speed
    return _summary(state)


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project_route(project_id: str):
    if any(jobs.is_job_running(f"{project_id}:{s}") for s in ("ingest", "transcribe", "translate", "tts", "assemble")):
        raise HTTPException(status_code=409, detail="Đang có job chạy — đợi xong đã")
    try:
        state = pj.load_project(project_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    if any(_episode_busy(project_id, ep.episode_id) for ep in state.episodes):
        raise HTTPException(status_code=409, detail="Đang có job chạy — đợi xong đã")
    try:
        pj.delete_project(project_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


def _read_cues_sub_manifest(root: Path) -> tuple[list[SrtCue], Optional[str], Optional[str], list[dict]]:
    zh_cues = load_srt(root / "sub_zh.srt")
    vi_cues = {c.id: c.text for c in load_srt(root / "sub_vi.srt")}
    cues = [
        SrtCue(id=c.id, start=c.start, end=c.end, text=c.text, text_vi=vi_cues.get(c.id))
        for c in zh_cues
    ]
    sub_zh = (root / "sub_zh.srt").read_text(encoding="utf-8") if (root / "sub_zh.srt").exists() else None
    sub_vi = (root / "sub_vi.srt").read_text(encoding="utf-8") if (root / "sub_vi.srt").exists() else None
    tts_manifest: list[dict] = []
    manifest_path = root / "audio" / "manifest.json"
    if manifest_path.exists():
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(raw_manifest, list):
            tts_manifest = raw_manifest
    return cues, sub_zh, sub_vi, tts_manifest


def _build_episode_detail(project_id: str, episode: Episode) -> EpisodeDetail:
    root = pj.episode_dir(project_id, episode.episode_id)
    cues, sub_zh, sub_vi, tts_manifest = _read_cues_sub_manifest(root)
    video_url = None
    if episode.video_relpath:
        video_url = f"/api/projects/{project_id}/assets/episodes/{episode.episode_id}/{episode.video_relpath}"
    return EpisodeDetail(
        episode=episode,
        current_stage=pj.episode_current_stage(episode),
        video_url=video_url,
        cues=cues,
        sub_zh=sub_zh,
        sub_vi=sub_vi,
        tts_manifest=tts_manifest,
    )


@app.get("/api/projects/{project_id}", response_model=ProjectDetailResponse)
def project_detail(project_id: str):
    try:
        state = pj.load_project(project_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    root = pj.project_dir(project_id)

    if state.project_type == "multi" or state.split_mode:
        entity: dict[str, str] = {}
        dict_path = pj.entity_dict_path(project_id)
        if dict_path.exists():
            raw = json.loads(dict_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                entity = {str(k): str(v) for k, v in raw.items()}
        episodes = [_build_episode_detail(project_id, ep) for ep in sorted(state.episodes, key=lambda e: e.order)]
        return ProjectDetailResponse(
            project=state,
            current_stage=pj.current_stage(state),
            entity_dict=entity,
            logs=[LogEntry(**e) for e in pj.read_logs(project_id)],
            episodes=episodes,
        )

    cues, sub_zh, sub_vi, tts_manifest = _read_cues_sub_manifest(root)
    entity = {}
    dict_path = root / "entity_dict.json"
    if dict_path.exists():
        raw = json.loads(dict_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            entity = {str(k): str(v) for k, v in raw.items()}

    video_url = None
    if state.video_relpath:
        video_url = f"/api/projects/{project_id}/assets/{state.video_relpath}"

    return ProjectDetailResponse(
        project=state,
        current_stage=pj.current_stage(state),
        video_url=video_url,
        cues=cues,
        entity_dict=entity,
        sub_zh=sub_zh,
        sub_vi=sub_vi,
        tts_manifest=tts_manifest,
        logs=[LogEntry(**e) for e in pj.read_logs(project_id)],
    )


@app.get("/api/projects/{project_id}/logs", response_model=list[LogEntry])
def project_logs(project_id: str, limit: int = 40):
    return [LogEntry(**e) for e in pj.read_logs(project_id, limit)]


# ------------------------------------------------------------------ Episodes (dự án dài tập)


@app.post("/api/projects/{project_id}/episodes", response_model=EpisodeDetail, status_code=201)
def create_episode_route(project_id: str, body: CreateEpisodeRequest | None = None):
    try:
        with pj.locked_project(project_id) as state:
            if state.project_type != "multi":
                raise HTTPException(status_code=409, detail="Dự án này không phải dạng dài tập")
            episode = pj.create_episode(
                state,
                title=(body.title if body else ""),
                insert_after_episode_id=(body.insert_after_episode_id if body else None),
            )
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return _build_episode_detail(project_id, episode)


@app.patch("/api/projects/{project_id}/episodes/reorder", response_model=list[EpisodeDetail])
def reorder_episodes_route(project_id: str, body: ReorderEpisodesRequest):
    with pj.locked_project(project_id) as state:
        by_id = {ep.episode_id: ep for ep in state.episodes}
        if set(by_id) != set(body.episode_ids):
            raise HTTPException(status_code=400, detail="Danh sách episode_ids không khớp các tập hiện có")
        for idx, episode_id in enumerate(body.episode_ids):
            by_id[episode_id].order = idx
        state.episodes.sort(key=lambda e: e.order)
        episodes = list(state.episodes)
    return [_build_episode_detail(project_id, ep) for ep in episodes]


@app.delete("/api/projects/{project_id}/episodes/{episode_id}", status_code=204)
def delete_episode_route(project_id: str, episode_id: str):
    if _episode_busy(project_id, episode_id):
        raise HTTPException(status_code=409, detail="Đang có job chạy cho tập này — đợi xong đã")
    with pj.locked_project(project_id) as state:
        _get_episode_or_404(state, episode_id)
        state.episodes = [ep for ep in state.episodes if ep.episode_id != episode_id]
        for idx, ep in enumerate(sorted(state.episodes, key=lambda e: e.order)):
            ep.order = idx
    shutil.rmtree(pj.episode_dir(project_id, episode_id), ignore_errors=True)
    pj.append_log(project_id, "ingest", f"[{episode_id}] Đã xoá tập")


# ------------------------------------------------------------------ Split (video dài → nhiều đoạn, mỗi đoạn 1 draft riêng)


@app.post("/api/projects/{project_id}/split", response_model=list[EpisodeDetail], status_code=201)
def split_project_route(project_id: str, body: SplitProjectRequest):
    """Cắt video của 1 dự án ĐƠN thành N đoạn theo `split_points_s` (N-1 mốc,
    giây) — mỗi đoạn trở thành 1 Episode tự chạy pipeline TUẦN TỰ (đoạn sau
    chỉ bắt đầu khi đoạn trước ráp draft xong, xem `_maybe_chain_episode`
    nhánh `split_mode`), ra 1 draft CapCut RIÊNG mỗi đoạn — khác dự án "dài
    tập" (nhiều nguồn gộp chung 1 draft)."""
    state = pj.load_project(project_id)
    if state.project_type != "single":
        raise HTTPException(status_code=409, detail="Chỉ áp dụng cho dự án đơn")
    if state.split_mode:
        raise HTTPException(status_code=409, detail="Dự án này đã được chia đoạn rồi")
    _require_done(state, "ingest", "Chưa upload video")
    # Chỉ chặn khi Whisper THẬT SỰ đang chạy (tránh race) — "pending"/"failed"/
    # "done" đều cho chia được: 1 khi bật split_mode, các stage cấp PROJECT
    # (transcribe/translate/tts/assemble) không còn ai đọc tới nữa (chỉ
    # episodes mới có ý nghĩa — xem comment ProjectState.split_mode), nên kết
    # quả Whisper cũ (nếu có) chỉ nằm im vô hại trên đĩa, không cần chặn.
    if jobs.is_job_running(f"{project_id}:transcribe"):
        raise HTTPException(status_code=409, detail="Whisper đang chạy cho video gốc — đợi xong hoặc bấm Dừng trước")
    if not state.video_relpath or not state.duration_sec:
        raise HTTPException(status_code=409, detail="Chưa có video/độ dài video")

    duration = state.duration_sec
    points = sorted(body.split_points_s)
    if not points:
        raise HTTPException(status_code=400, detail="Cần ít nhất 1 mốc cắt (chia thành ít nhất 2 đoạn)")
    if len(set(points)) != len(points):
        raise HTTPException(status_code=400, detail="Các mốc cắt phải khác nhau")
    if any(p <= 0 or p >= duration for p in points):
        raise HTTPException(status_code=400, detail="Mốc cắt phải nằm trong khoảng thời lượng video")

    root = pj.project_dir(project_id)
    video_path = root / state.video_relpath
    ext = Path(state.video_relpath).suffix
    n = len(points) + 1
    boundaries = [0.0, *points, duration]

    with pj.locked_project(project_id) as s:
        new_episodes = [pj.create_episode(s, title=f"Đoạn {i + 1}") for i in range(n)]
        s.split_mode = True

    out_paths = [pj.episode_dir(project_id, ep.episode_id) / f"video{ext}" for ep in new_episodes]
    try:
        video_split_stage.split_video(video_path, points, out_paths, duration)
    except video_split_stage.VideoSplitError as err:
        new_ids = {ep.episode_id for ep in new_episodes}
        with pj.locked_project(project_id) as s:
            s.episodes = [e for e in s.episodes if e.episode_id not in new_ids]
            s.split_mode = False
        for ep in new_episodes:
            shutil.rmtree(pj.episode_dir(project_id, ep.episode_id), ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(err)) from err

    with pj.locked_project(project_id) as s:
        for i, ep_stub in enumerate(new_episodes):
            ep = pj.find_episode(s, ep_stub.episode_id)
            ep.video_relpath = out_paths[i].name
            ep.original_filename = f"{state.original_filename or state.video_relpath} — đoạn {i + 1}/{n}"
            ep.duration_sec = boundaries[i + 1] - boundaries[i]
            ep.stages["ingest"] = StageRecord(status=StageStatus.done, output=ep.video_relpath, at=datetime.now())
        result_episodes = sorted(s.episodes, key=lambda e: e.order)

    pj.append_log(project_id, "ingest", f"Đã chia thành {n} đoạn")

    if state.auto_pipeline:
        _start_episode_transcribe(project_id, result_episodes[0].episode_id, state.auto_engine)

    return [_build_episode_detail(project_id, ep) for ep in result_episodes]


def _mark_cancelled_episode(project_id: str, episode_id: str, stage: str, job: jobs.JobState) -> None:
    job.status = "cancelled"
    for it in job.items:
        if it.status == "running":
            it.status = "failed"
            it.error = "Đã dừng"
    with pj.locked_project(project_id) as s:
        ep = pj.find_episode(s, episode_id)
        ep.stages[stage].status = StageStatus.failed
        ep.stages[stage].error = "Đã dừng theo yêu cầu người dùng"
    pj.append_log(project_id, stage, f"[{episode_id}] Đã dừng theo yêu cầu người dùng")


def _finalize_episode_ingest(
    project_id: str, episode_id: str, root: Path, dest: Path, original: str, duration: float | None, log_msg: str
) -> None:
    rel = dest.name
    # Không xoá entity_dict.json ở đây — dùng CHUNG cấp project cho mọi tập
    # trong series, không phải leftover riêng của tập này (khác _finalize_ingest).
    for leftover in ("sub_zh.srt", "sub_vi.srt", "background.wav", "background_original.wav"):
        (root / leftover).unlink(missing_ok=True)
    shutil.rmtree(root / "audio", ignore_errors=True)
    with pj.locked_project(project_id) as state:
        episode = pj.find_episode(state, episode_id)
        pj.reset_episode_from(episode, "transcribe")
        episode.original_filename = original
        episode.video_relpath = rel
        episode.duration_sec = duration
        episode.stages["ingest"] = StageRecord(
            status=StageStatus.done,
            output=rel,
            progress=original,
            error=None,
            at=datetime.now(),
        )
    pj.append_log(project_id, "ingest", f"[{episode_id}] {log_msg}")
    _maybe_chain_episode(project_id, episode_id, "ingest")


@app.post("/api/projects/{project_id}/episodes/{episode_id}/ingest")
async def ingest_episode_video(project_id: str, episode_id: str, file: UploadFile = File(...)):
    state = pj.load_project(project_id)
    _get_episode_or_404(state, episode_id)
    if _episode_busy(project_id, episode_id):
        raise HTTPException(status_code=409, detail="Đang có job chạy — đợi xong đã")
    root = pj.episode_dir(project_id, episode_id)
    try:
        dest, original, duration = await ingest_stage.save_uploaded_video(root, file)
    except ValueError as err:
        with pj.locked_project(project_id) as s:
            ep = pj.find_episode(s, episode_id)
            ep.stages["ingest"].status = StageStatus.failed
            ep.stages["ingest"].error = str(err)
        raise HTTPException(status_code=400, detail=str(err)) from err

    _finalize_episode_ingest(project_id, episode_id, root, dest, original, duration, f"upload {original}")
    return {"status": "ok", "filename": original, "path": dest.name, "duration_sec": duration}


@app.post("/api/projects/{project_id}/episodes/{episode_id}/ingest/url", status_code=202)
def ingest_episode_video_url(project_id: str, episode_id: str, body: IngestUrlRequest):
    state = pj.load_project(project_id)
    _get_episode_or_404(state, episode_id)
    if _episode_busy(project_id, episode_id):
        raise HTTPException(status_code=409, detail="Đang có job chạy — đợi xong đã")
    root = pj.episode_dir(project_id, episode_id)
    share_url = body.url

    def target(job: jobs.JobState) -> None:
        job.items = [jobs.JobItem(id="fetch_url", label="Tải video từ link")]
        job.items[0].status = "running"
        with pj.locked_project(project_id) as s:
            ep = pj.find_episode(s, episode_id)
            ep.stages["ingest"].status = StageStatus.running
            ep.stages["ingest"].error = None

        def on_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            dest, original, duration = fetch_url_stage.download_video_from_share(root, share_url, on_progress=on_progress)
        except jobs.JobCancelled:
            _mark_cancelled_episode(project_id, episode_id, "ingest", job)
            return
        except fetch_url_stage.FetchUrlError as err:
            job.items[0].status = "failed"
            job.items[0].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                ep = pj.find_episode(s, episode_id)
                ep.stages["ingest"].status = StageStatus.failed
                ep.stages["ingest"].error = str(err)
            pj.append_log(project_id, "ingest", f"[{episode_id}] {err}")
            return
        except Exception as err:
            logger.exception("Tải video từ link lỗi {} / {}", project_id, episode_id)
            job.items[0].status = "failed"
            job.items[0].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                ep = pj.find_episode(s, episode_id)
                ep.stages["ingest"].status = StageStatus.failed
                ep.stages["ingest"].error = str(err)
            pj.append_log(project_id, "ingest", f"[{episode_id}] Lỗi: {err}")
            return

        job.items[0].status = "done"
        job.done_count = job.total
        job.status = "done"
        _finalize_episode_ingest(project_id, episode_id, root, dest, original, duration, f"tải từ link: {original}")

    job = jobs.start_job(_episode_job_key(project_id, episode_id, "ingest"), 1, target)
    if job is None:
        raise HTTPException(status_code=409, detail="Đang tải video cho tập này")
    return {"status": "started"}


def _start_episode_transcribe(
    project_id: str, episode_id: str, engine: str, crop_region: list[float] | None = None
) -> jobs.JobState | None:
    state = pj.load_project(project_id)
    episode = pj.find_episode(state, episode_id)
    if not episode.video_relpath:
        return None
    root = pj.episode_dir(project_id, episode_id)
    video_path = root / episode.video_relpath
    total = max(1, int(episode.duration_sec or 1))

    engine, stage_fn, engine_label = _resolve_transcribe_engine(engine)
    # `crop_region` (x,y,w,h dạng phân số 0-1, người dùng tự khoanh trên
    # preview video) chỉ engine "ocr" hiểu — 2 engine kia không nhận tham số
    # này, nên chỉ truyền khi thật sự dùng OCR để không vỡ signature của chúng.
    extra_kwargs = {"crop_region": tuple(crop_region)} if engine == "ocr" and crop_region else {}

    def target(job: jobs.JobState) -> None:
        job.items = [jobs.JobItem(id=engine, label=engine_label)]
        job.items[0].status = "running"
        job.current_label = engine_label
        (root / "sub_vi.srt").unlink(missing_ok=True)
        with pj.locked_project(project_id) as s:
            ep = pj.find_episode(s, episode_id)
            pj.reset_episode_from(ep, "translate")
            ep.stages["transcribe"].status = StageStatus.running
            ep.stages["transcribe"].error = None
            ep.stages["transcribe"].engine = engine

        def on_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            cues, lang = stage_fn(video_path, root / "sub_zh.srt", on_progress=on_progress, **extra_kwargs)
        except jobs.JobCancelled:
            _mark_cancelled_episode(project_id, episode_id, "transcribe", job)
            return
        except Exception as err:
            logger.exception("{} lỗi {} / {}", engine_label, project_id, episode_id)
            job.items[0].status = "failed"
            job.items[0].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                ep = pj.find_episode(s, episode_id)
                ep.stages["transcribe"].status = StageStatus.failed
                ep.stages["transcribe"].error = str(err)
            pj.append_log(project_id, "transcribe", f"[{episode_id}] Lỗi: {err}")
            return

        job.items[0].status = "done"
        job.done_count = job.total
        job.status = "done"
        with pj.locked_project(project_id) as s:
            ep = pj.find_episode(s, episode_id)
            ep.stages["transcribe"].status = StageStatus.done
            ep.stages["transcribe"].output = "sub_zh.srt"
            ep.stages["transcribe"].progress = f"{len(cues)} câu · {lang}"
            ep.stages["transcribe"].error = None
            ep.stages["transcribe"].engine = engine
            ep.stages["transcribe"].at = datetime.now()
        pj.append_log(project_id, "transcribe", f"[{episode_id}] {len(cues)} câu · {lang}")
        _maybe_chain_episode(project_id, episode_id, "transcribe")

    return jobs.start_job(_episode_job_key(project_id, episode_id, "transcribe"), total, target)


@app.post("/api/projects/{project_id}/episodes/{episode_id}/transcribe", status_code=202)
def start_episode_transcribe_route(project_id: str, episode_id: str, body: StartTranscribeRequest | None = None):
    state = pj.load_project(project_id)
    episode = _get_episode_or_404(state, episode_id)
    _require_episode_done(episode, "ingest", "Chưa upload video")
    if not episode.video_relpath:
        raise HTTPException(status_code=409, detail="Chưa có file video")
    engine, _, engine_label = _resolve_transcribe_engine(body.engine if body else None)
    job = _start_episode_transcribe(project_id, episode_id, engine, body.crop_region if body else None)
    if job is None:
        raise HTTPException(status_code=409, detail=f"{engine_label} đang chạy cho tập này")
    return {"status": "started"}


def _start_episode_translate(project_id: str, episode_id: str) -> jobs.JobState | None:
    root = pj.episode_dir(project_id, episode_id)
    n = len(load_srt(root / "sub_zh.srt")) or 1
    dict_path = pj.entity_dict_path(project_id)

    def target(job: jobs.JobState) -> None:
        job.items = [jobs.JobItem(id="gemini", label="Gemini zh→vi")]
        job.items[0].status = "running"
        job.current_label = "Gemini"
        shutil.rmtree(root / "audio", ignore_errors=True)
        with pj.locked_project(project_id) as s:
            ep = pj.find_episode(s, episode_id)
            pj.reset_episode_from(ep, "tts")
            ep.stages["translate"].status = StageStatus.running
            ep.stages["translate"].error = None

        def on_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            _, _, entity = translate_stage.translate_project(root, on_progress=on_progress, entity_dict_path=dict_path)
        except jobs.JobCancelled:
            _mark_cancelled_episode(project_id, episode_id, "translate", job)
            return
        except (translate_stage.LLMNotConfigured, translate_stage.GeminiQuotaError) as err:
            job.items[0].status = "failed"
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                ep = pj.find_episode(s, episode_id)
                ep.stages["translate"].status = StageStatus.failed
                ep.stages["translate"].error = str(err)
            pj.append_log(project_id, "translate", f"[{episode_id}] {err}")
            return
        except Exception as err:
            logger.exception("Gemini lỗi {} / {}", project_id, episode_id)
            job.items[0].status = "failed"
            job.items[0].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                ep = pj.find_episode(s, episode_id)
                ep.stages["translate"].status = StageStatus.failed
                ep.stages["translate"].error = str(err)
            pj.append_log(project_id, "translate", f"[{episode_id}] Lỗi: {err}")
            return

        job.items[0].status = "done"
        job.done_count = job.total
        job.status = "done"
        with pj.locked_project(project_id) as s:
            ep = pj.find_episode(s, episode_id)
            ep.stages["translate"].status = StageStatus.done
            ep.stages["translate"].output = "sub_vi.srt"
            ep.stages["translate"].progress = f"{n} câu · {len(entity)} tên riêng"
            ep.stages["translate"].error = None
            ep.stages["translate"].at = datetime.now()
        pj.append_log(project_id, "translate", f"[{episode_id}] {n} câu · {len(entity)} tên riêng")
        _maybe_chain_episode(project_id, episode_id, "translate")

    return jobs.start_job(_episode_job_key(project_id, episode_id, "translate"), n, target)


@app.post("/api/projects/{project_id}/episodes/{episode_id}/translate", status_code=202)
def start_episode_translate_route(project_id: str, episode_id: str):
    state = pj.load_project(project_id)
    episode = _get_episode_or_404(state, episode_id)
    _require_episode_done(episode, "transcribe", "Chưa có phụ đề tiếng Trung — chạy Whisper trước")
    job = _start_episode_translate(project_id, episode_id)
    if job is None:
        raise HTTPException(status_code=409, detail="Gemini đang chạy cho tập này")
    return {"status": "started"}


def _start_episode_tts(
    project_id: str, episode_id: str, voice: str, retry_only: bool = False, engine: str = "capcut"
) -> jobs.JobState | None:
    root = pj.episode_dir(project_id, episode_id)
    mod = _tts_module(engine)
    voices = _tts_voices(engine)
    engine_label = _tts_engine_label(engine)

    if voice not in {v["id"] for v in voices}:
        voice = mod.DEFAULT_VOICE
    voice_label = next((v["label"] for v in voices if v["id"] == voice), voice)

    if retry_only:
        manifest_path = root / "audio" / "manifest.json"
        if not manifest_path.exists():
            return None
        prev_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        n = sum(1 for m in prev_manifest if m.get("error")) or 1
    else:
        n = len(load_srt(root / "sub_vi.srt")) or 1

    def target(job: jobs.JobState) -> None:
        label = f"Thử lại câu lỗi · {voice_label}" if retry_only else f"{engine_label} · {voice_label}"
        job.items = [jobs.JobItem(id="tts", label=label)]
        job.items[0].status = "running"
        job.current_label = voice_label
        with pj.locked_project(project_id) as s:
            ep = pj.find_episode(s, episode_id)
            pj.reset_episode_from(ep, "tts")
            ep.stages["tts"].status = StageStatus.running
            ep.stages["tts"].error = None

        def on_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            if retry_only:
                _, manifest = mod.retry_failed_segments(root, voice=voice, on_progress=on_progress)
            else:
                _, manifest = mod.tts_project(root, voice=voice, on_progress=on_progress)
        except jobs.JobCancelled:
            _mark_cancelled_episode(project_id, episode_id, "tts", job)
            return
        except mod.TTSError as err:
            job.items[0].status = "failed"
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                ep = pj.find_episode(s, episode_id)
                ep.stages["tts"].status = StageStatus.failed
                ep.stages["tts"].error = str(err)
            pj.append_log(project_id, "tts", f"[{episode_id}] {err}")
            return
        except Exception as err:
            logger.exception("TTS lỗi {} / {}", project_id, episode_id)
            job.items[0].status = "failed"
            job.items[0].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                ep = pj.find_episode(s, episode_id)
                ep.stages["tts"].status = StageStatus.failed
                ep.stages["tts"].error = str(err)
            pj.append_log(project_id, "tts", f"[{episode_id}] Lỗi: {err}")
            return

        ok = sum(1 for m in manifest if m.get("path"))
        failed = sum(1 for m in manifest if m.get("error"))
        job.items[0].status = "done"
        job.done_count = job.total
        job.status = "done"
        with pj.locked_project(project_id) as s:
            ep = pj.find_episode(s, episode_id)
            ep.stages["tts"].status = StageStatus.done
            ep.stages["tts"].output = "audio/manifest.json"
            ep.stages["tts"].progress = f"{ok}/{len(manifest)} câu · {voice_label}" + (f" · {failed} lỗi" if failed else "")
            ep.stages["tts"].engine = engine
            ep.stages["tts"].error = None
            ep.stages["tts"].at = datetime.now()
            s.auto_voice = voice  # nhớ giọng vừa dùng — mặc định cho tập kế tiếp / lần sau
            s.auto_tts_engine = engine
        pj.append_log(project_id, "tts", f"[{episode_id}] {ok}/{len(manifest)} câu · {voice_label}")
        _maybe_chain_episode(project_id, episode_id, "tts")

    return jobs.start_job(_episode_job_key(project_id, episode_id, "tts"), n, target)


@app.post("/api/projects/{project_id}/episodes/{episode_id}/tts", status_code=202)
def start_episode_tts_route(project_id: str, episode_id: str, body: StartTTSRequest | None = None):
    state = pj.load_project(project_id)
    episode = _get_episode_or_404(state, episode_id)
    _require_episode_done(episode, "translate", "Chưa có bản dịch tiếng Việt — chạy Gemini trước")
    voice = body.voice if body and body.voice else ""
    retry_only = bool(body and body.retry_failed_only)
    engine = (episode.stages["tts"].engine or "capcut") if retry_only else ("vieneu" if body and body.engine == "vieneu" else "capcut")
    if retry_only:
        manifest_path = pj.episode_dir(project_id, episode_id) / "audio" / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=409, detail="Chưa chạy TTS lần nào")
    job = _start_episode_tts(project_id, episode_id, voice, retry_only=retry_only, engine=engine)
    if job is None:
        raise HTTPException(status_code=409, detail="TTS đang chạy cho tập này")
    return {"status": "started"}


def _start_episode_assemble(
    project_id: str,
    episode_id: str,
    audio_mode: str = "original",
    min_video_speed: float = 0.85,
    original_audio_volume_db: float = -13.0,
) -> jobs.JobState | None:
    """Ráp draft CapCut RIÊNG cho đúng 1 tập/đoạn — dùng cho dự án "split"
    (mỗi đoạn video dài ra 1 draft nhỏ, không gộp chung như `_start_assemble_multi`
    của dự án "dài tập"). Tái dùng thẳng `assemble_stage.assemble_project`
    (bản đơn nguồn) với draft_name riêng theo thứ tự đoạn."""
    state = pj.load_project(project_id)
    episode = pj.find_episode(state, episode_id)
    if not episode.video_relpath:
        return None
    root = pj.episode_dir(project_id, episode_id)
    draft_name = f"{project_id}_p{episode.order + 1}"

    mute_original_audio = audio_mode == "mute"
    bg_filename = "background.wav" if audio_mode == "separated" else "background_original.wav"

    def target(job: jobs.JobState) -> None:
        if mute_original_audio:
            bg_label = "Tắt âm thanh gốc"
        elif audio_mode == "separated":
            bg_label = "Tách nhạc nền (demucs)"
        else:
            bg_label = "Trích audio gốc (giữ nguyên)"
        job.items = [
            jobs.JobItem(id="background", label=bg_label),
            jobs.JobItem(id="draft", label="Dựng draft CapCut"),
        ]
        job.current_label = job.items[0].label
        with pj.locked_project(project_id) as s:
            ep = pj.find_episode(s, episode_id)
            ep.stages["assemble"].status = StageStatus.running
            ep.stages["assemble"].error = None

        video_path = root / episode.video_relpath
        bg_path = root / bg_filename

        def on_bg_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.current_label = label

        if mute_original_audio:
            bg_path = None
        elif not bg_path.exists():
            job.items[0].status = "running"
            try:
                if audio_mode == "separated":
                    dub_audio_stage.extract_background(video_path, bg_path, on_progress=on_bg_progress, job=job)
                else:
                    dub_audio_stage.extract_original_audio(video_path, bg_path, on_progress=on_bg_progress)
            except jobs.JobCancelled:
                _mark_cancelled_episode(project_id, episode_id, "assemble", job)
                return
            except dub_audio_stage.DubAudioError as err:
                job.items[0].status = "failed"
                job.status = "failed"
                job.error = str(err)
                with pj.locked_project(project_id) as s:
                    ep = pj.find_episode(s, episode_id)
                    ep.stages["assemble"].status = StageStatus.failed
                    ep.stages["assemble"].error = str(err)
                pj.append_log(project_id, "assemble", f"[{episode_id}] {err}")
                return
        job.items[0].status = "done"

        job.items[1].status = "running"
        job.current_label = "Dựng draft"

        def on_draft_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            manifest_path = root / "audio" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
            vi_cues = load_srt(root / "sub_vi.srt")
            draft_path = assemble_stage.assemble_project(
                video_path=video_path,
                background_path=bg_path,
                manifest=manifest,
                vi_cues=vi_cues,
                audio_dir=root / "audio",
                draft_name=draft_name,
                on_progress=on_draft_progress,
                mute_original_audio=mute_original_audio,
                min_video_speed=min_video_speed,
                background_volume=_resolve_background_volume_linear(audio_mode, original_audio_volume_db),
            )
        except jobs.JobCancelled:
            _mark_cancelled_episode(project_id, episode_id, "assemble", job)
            return
        except assemble_stage.AssembleError as err:
            job.items[1].status = "failed"
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                ep = pj.find_episode(s, episode_id)
                ep.stages["assemble"].status = StageStatus.failed
                ep.stages["assemble"].error = str(err)
            pj.append_log(project_id, "assemble", f"[{episode_id}] {err}")
            return
        except Exception as err:
            logger.exception("Assemble (split) lỗi {} / {}", project_id, episode_id)
            job.items[1].status = "failed"
            job.items[1].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                ep = pj.find_episode(s, episode_id)
                ep.stages["assemble"].status = StageStatus.failed
                ep.stages["assemble"].error = str(err)
            pj.append_log(project_id, "assemble", f"[{episode_id}] Lỗi: {err}")
            return

        job.items[1].status = "done"
        job.status = "done"
        with pj.locked_project(project_id) as s:
            ep = pj.find_episode(s, episode_id)
            ep.stages["assemble"].status = StageStatus.done
            ep.stages["assemble"].output = str(draft_path)
            ep.stages["assemble"].progress = f"draft: {draft_name}"
            ep.stages["assemble"].error = None
            ep.stages["assemble"].at = datetime.now()
            s.auto_audio_mode = audio_mode
            s.auto_min_video_speed = min_video_speed
            s.auto_original_audio_volume_db = original_audio_volume_db
        pj.append_log(project_id, "assemble", f"[{episode_id}] draft → {draft_path}")
        _maybe_chain_episode(project_id, episode_id, "assemble")

    return jobs.start_job(_episode_job_key(project_id, episode_id, "assemble"), 1, target)


@app.post("/api/projects/{project_id}/episodes/{episode_id}/assemble", status_code=202)
def start_episode_assemble_route(project_id: str, episode_id: str, body: StartAssembleRequest | None = None):
    state = pj.load_project(project_id)
    episode = _get_episode_or_404(state, episode_id)
    _require_episode_done(episode, "tts", "Chưa có audio TTS — chạy TTS trước")
    if not episode.video_relpath:
        raise HTTPException(status_code=409, detail="Chưa có file video")
    job = _start_episode_assemble(
        project_id,
        episode_id,
        audio_mode=(body.audio_mode if body else "original"),
        min_video_speed=(body.min_video_speed if body else 0.85),
        original_audio_volume_db=(body.original_audio_volume_db if body else -13.0),
    )
    if job is None:
        raise HTTPException(status_code=409, detail="Assemble đang chạy cho tập này")
    return {"status": "started"}


def _episode_cue_response(root: Path, cue_id: int) -> SrtCue:
    zh = next((c for c in load_srt(root / "sub_zh.srt") if c.id == cue_id), None)
    vi = next((c for c in load_srt(root / "sub_vi.srt") if c.id == cue_id), None)
    if zh is None and vi is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy câu #{cue_id}")
    base = zh or vi
    return SrtCue(id=cue_id, start=base.start, end=base.end, text=zh.text if zh else "", text_vi=vi.text if vi else None)


@app.patch("/api/projects/{project_id}/episodes/{episode_id}/cues/{cue_id}", response_model=SrtCue)
def update_episode_cue_route(project_id: str, episode_id: str, cue_id: int, body: UpdateCueRequest):
    state = pj.load_project(project_id)
    episode = _get_episode_or_404(state, episode_id)
    _require_episode_done(episode, "translate", "Chưa có bản dịch — chạy Gemini trước")
    if _episode_busy(project_id, episode_id):
        raise HTTPException(status_code=409, detail="Đang có job chạy — đợi xong đã")
    root = pj.episode_dir(project_id, episode_id)
    try:
        update_cue_text(root / "sub_vi.srt", cue_id, body.text_vi)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    pj.append_log(project_id, "translate", f"[{episode_id}] Sửa tay câu #{cue_id}")
    return _episode_cue_response(root, cue_id)


@app.post("/api/projects/{project_id}/episodes/{episode_id}/cues/{cue_id}/retranslate", response_model=SrtCue)
def retranslate_episode_cue_route(project_id: str, episode_id: str, cue_id: int):
    state = pj.load_project(project_id)
    episode = _get_episode_or_404(state, episode_id)
    _require_episode_done(episode, "translate", "Chưa có bản dịch — chạy Gemini trước")
    if _episode_busy(project_id, episode_id):
        raise HTTPException(status_code=409, detail="Đang có job chạy — đợi xong đã")
    root = pj.episode_dir(project_id, episode_id)
    try:
        text_vi = translate_stage.retranslate_cue(root, cue_id, entity_dict_path=pj.entity_dict_path(project_id))
    except translate_stage.LLMNotConfigured as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except translate_stage.GeminiQuotaError as err:
        raise HTTPException(status_code=429, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    pj.append_log(project_id, "translate", f"[{episode_id}] Dịch lại câu #{cue_id}: {text_vi[:60]}")
    return _episode_cue_response(root, cue_id)


@app.post("/api/projects/{project_id}/episodes/{episode_id}/cues/{cue_id}/tts", response_model=TTSManifestEntryResponse)
def tts_episode_cue_route(project_id: str, episode_id: str, cue_id: int, body: TTSCueRequest | None = None):
    state = pj.load_project(project_id)
    episode = _get_episode_or_404(state, episode_id)
    _require_episode_done(episode, "translate", "Chưa có bản dịch — chạy Gemini trước")
    if _episode_busy(project_id, episode_id):
        raise HTTPException(status_code=409, detail="Đang có job chạy — đợi xong đã")
    root = pj.episode_dir(project_id, episode_id)
    voice = body.voice if body and body.voice else ""
    engine = episode.stages["tts"].engine or "capcut"
    mod = _tts_module(engine)
    voices = _tts_voices(engine)
    if voice not in {v["id"] for v in voices}:
        voice = mod.DEFAULT_VOICE
    try:
        entry = mod.tts_single_segment(root, cue_id, voice=voice)
    except mod.TTSError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    pj.append_log(project_id, "tts", f"[{episode_id}] Đọc lại câu #{cue_id}")
    return TTSManifestEntryResponse(**entry)


@app.post("/api/projects/{project_id}/episodes/{episode_id}/jobs/{stage}/cancel")
def cancel_episode_job_route(project_id: str, episode_id: str, stage: str):
    if stage not in EPISODE_STAGES:
        raise HTTPException(status_code=404, detail="Stage không hỗ trợ job")
    ok = jobs.request_cancel(_episode_job_key(project_id, episode_id, stage))
    if not ok:
        raise HTTPException(status_code=409, detail="Không có job nào đang chạy cho stage này")
    return {"status": "cancelling"}


@app.get("/api/projects/{project_id}/episodes/{episode_id}/jobs/{stage}", response_model=JobStatusResponse)
def episode_job_status(project_id: str, episode_id: str, stage: str):
    if stage not in EPISODE_STAGES:
        raise HTTPException(status_code=404, detail="Stage không hỗ trợ job")
    key = _episode_job_key(project_id, episode_id, stage)
    job = jobs.get_job(key)
    if job is None:
        state = pj.load_project(project_id)
        episode = _get_episode_or_404(state, episode_id)
        orphaned = episode.stages[stage].status == StageStatus.running
        if orphaned:
            with pj.locked_project(project_id) as s:
                ep = pj.find_episode(s, episode_id)
                if ep.stages[stage].status == StageStatus.running:
                    ep.stages[stage].status = StageStatus.failed
                    ep.stages[stage].error = "Phiên trước bị gián đoạn (server restart) — bấm chạy lại."
            pj.append_log(project_id, stage, f"[{episode_id}] Phiên trước bị gián đoạn (server restart)")
        return JobStatusResponse(registered=False, orphaned=orphaned)
    return JobStatusResponse(
        registered=True,
        status=job.status,
        total=job.total,
        done_count=job.done_count,
        current_label=job.current_label,
        items=[JobItemResponse(id=it.id, label=it.label, status=it.status, error=it.error) for it in job.items],
        error=job.error,
        started_at=job.started_at,
    )


# ------------------------------------------------------------------ Ingest


def _finalize_ingest(project_id: str, root: Path, dest: Path, original: str, duration: float | None, log_msg: str) -> None:
    rel = dest.name
    for leftover in ("sub_zh.srt", "sub_vi.srt", "entity_dict.json", "background.wav", "background_original.wav"):
        (root / leftover).unlink(missing_ok=True)
    shutil.rmtree(root / "audio", ignore_errors=True)
    with pj.locked_project(project_id) as state:
        pj.reset_from(state, "transcribe")
        state.original_filename = original
        state.video_relpath = rel
        state.duration_sec = duration
        state.stages["ingest"] = StageRecord(
            status=StageStatus.done,
            output=rel,
            progress=original,
            error=None,
            at=datetime.now(),
        )
    pj.append_log(project_id, "ingest", log_msg)
    _maybe_chain(project_id, "ingest")


@app.post("/api/projects/{project_id}/ingest")
async def ingest_video(project_id: str, file: UploadFile = File(...)):
    if any(jobs.is_job_running(f"{project_id}:{s}") for s in ("ingest", "transcribe", "translate", "tts", "assemble")):
        raise HTTPException(status_code=409, detail="Đang có job chạy — đợi xong đã")
    root = pj.project_dir(project_id)
    try:
        dest, original, duration = await ingest_stage.save_uploaded_video(root, file)
    except ValueError as err:
        with pj.locked_project(project_id) as state:
            state.stages["ingest"].status = StageStatus.failed
            state.stages["ingest"].error = str(err)
        raise HTTPException(status_code=400, detail=str(err)) from err

    _finalize_ingest(project_id, root, dest, original, duration, f"upload {original}")
    return {"status": "ok", "filename": original, "path": dest.name, "duration_sec": duration}


@app.post("/api/projects/{project_id}/ingest/url", status_code=202)
def ingest_video_url(project_id: str, body: IngestUrlRequest):
    if any(jobs.is_job_running(f"{project_id}:{s}") for s in ("ingest", "transcribe", "translate", "tts", "assemble")):
        raise HTTPException(status_code=409, detail="Đang có job chạy — đợi xong đã")
    root = pj.project_dir(project_id)
    share_url = body.url

    def target(job: jobs.JobState) -> None:
        job.items = [jobs.JobItem(id="fetch_url", label="Tải video từ link")]
        job.items[0].status = "running"
        with pj.locked_project(project_id) as s:
            s.stages["ingest"].status = StageStatus.running
            s.stages["ingest"].error = None

        def on_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            dest, original, duration = fetch_url_stage.download_video_from_share(root, share_url, on_progress=on_progress)
        except jobs.JobCancelled:
            _mark_cancelled(project_id, "ingest", job)
            return
        except fetch_url_stage.FetchUrlError as err:
            job.items[0].status = "failed"
            job.items[0].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.stages["ingest"].status = StageStatus.failed
                s.stages["ingest"].error = str(err)
            pj.append_log(project_id, "ingest", str(err))
            return
        except Exception as err:
            logger.exception("Tải video từ link lỗi {}", project_id)
            job.items[0].status = "failed"
            job.items[0].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.stages["ingest"].status = StageStatus.failed
                s.stages["ingest"].error = str(err)
            pj.append_log(project_id, "ingest", f"Lỗi: {err}")
            return

        job.items[0].status = "done"
        job.done_count = job.total
        job.status = "done"
        _finalize_ingest(project_id, root, dest, original, duration, f"tải từ link: {original}")

    job = jobs.start_job(f"{project_id}:ingest", 1, target)
    if job is None:
        raise HTTPException(status_code=409, detail="Đang tải video cho dự án này")
    return {"status": "started"}


@app.post("/api/projects/{project_id}/reveal-video")
def reveal_video(project_id: str):
    state = pj.load_project(project_id)
    if not state.video_relpath:
        raise HTTPException(status_code=404, detail="Chưa có file video")
    video_path = pj.project_dir(project_id) / state.video_relpath
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="File video không tồn tại trên đĩa")
    try:
        subprocess.run(["explorer", f"/select,{video_path}"], check=False)
    except FileNotFoundError as err:
        raise HTTPException(status_code=500, detail="Chỉ hỗ trợ mở Explorer trên Windows") from err
    return {"status": "ok"}


# ------------------------------------------------------------------ Transcribe / Translate jobs


def _start_transcribe(
    project_id: str, engine: str, crop_region: list[float] | None = None
) -> jobs.JobState | None:
    state = pj.load_project(project_id)
    if not state.video_relpath:
        return None
    root = pj.project_dir(project_id)
    video_path = root / state.video_relpath
    total = max(1, int(state.duration_sec or 1))

    engine, stage_fn, engine_label = _resolve_transcribe_engine(engine)
    extra_kwargs = {"crop_region": tuple(crop_region)} if engine == "ocr" and crop_region else {}

    def target(job: jobs.JobState) -> None:
        nonlocal extra_kwargs
        job.items = [jobs.JobItem(id=engine, label=engine_label)]
        job.items[0].status = "running"
        job.current_label = engine_label
        for leftover in ("sub_vi.srt", "entity_dict.json"):
            (root / leftover).unlink(missing_ok=True)
        with pj.locked_project(project_id) as s:
            pj.reset_from(s, "translate")
            s.stages["transcribe"].status = StageStatus.running
            s.stages["transcribe"].error = None
            s.stages["transcribe"].engine = engine

        def on_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        # Tự dò vùng phụ đề KHÍT (tái dùng `detect_subtitle_region` đã tune
        # kỹ) làm crop_region khi chưa khoanh tay — thay vì để mặc định crop
        # 25% đáy khung hình (rất rộng, nhất là video ngang). Đã xác nhận
        # thật (ảnh chụp khung hình thật): vùng crop mặc định quá rộng làm
        # cơ chế "bỏ qua khung giống khung trước" (tối ưu tốc độ, so khác
        # biệt pixel TRÊN TOÀN VÙNG CROP) bị LOÃNG tín hiệu — 2 câu phụ đề
        # khác hẳn nội dung nhưng cùng nằm trong vùng crop lớn, phần nền
        # xung quanh chữ chiếm đa số pixel, vẫn bị tính "giống nhau" nên bỏ
        # qua OCR, mất hẳn nhiều câu thật (video mẫu: chỉ 6/109 khung được
        # OCR thật). Crop khít lại làm % khác biệt tập trung đúng vào phần
        # CHỮ, nhạy đúng với thay đổi nội dung thật. Tái dùng
        # `export_blur_region` nếu lượt dò sớm sau ingest (`_start_early_blur_detect`)
        # đã kịp xong, đỡ dò lại lần nữa.
        # Best-effort thuần tuý — lỗi ở BẤT KỲ bước nào cũng chỉ log rồi bỏ
        # qua, KHÔNG được để lọt ra ngoài khối `target()`: 1 exception không
        # bắt ở đây làm chết hẳn thread job giữa chừng (job/`project.json`
        # kẹt mãi ở "running" vì đoạn code báo lỗi/mark-failed nằm sau, không
        # bao giờ chạy tới) — đã xác nhận thật đúng lỗi này 1 lần.
        if engine == "ocr" and not extra_kwargs:
            auto_region = None
            try:
                auto_region = pj.load_project(project_id).export_blur_region
                if not auto_region:
                    auto_region = ocr_stage.detect_subtitle_region(video_path)
            except Exception as err:
                logger.warning("_start_transcribe: tự dò vùng phụ đề thất bại cho '{}': {}", project_id, err)
                auto_region = None
            if auto_region:
                extra_kwargs = {"crop_region": tuple(auto_region)}

        # Video do "Dự án tự động" sinh ra dùng engine OCR nhưng KHÔNG có phụ
        # đề cứng in sẵn (OCR đọc đúng, chỉ là không có gì để đọc) — tự thử
        # lại 1 lần bằng Whisper cho ĐÚNG video này (không đổi engine mặc
        # định của cả dự án, không có người ngồi đổi engine tay). Cài đặt
        # thành vòng lặp thay vì gọi lại `_start_transcribe` — gọi lại sẽ bị
        # `jobs.start_job` từ chối vì cùng key job hiện vẫn "đang chạy"
        # (chính là thread đang thực thi đoạn code này).
        run_engine, run_fn, run_label, run_extra = engine, stage_fn, engine_label, extra_kwargs
        cues = lang = None
        for attempt in range(2):
            try:
                cues, lang = run_fn(video_path, root / "sub_zh.srt", on_progress=on_progress, **run_extra)
                break
            except jobs.JobCancelled:
                _mark_cancelled(project_id, "transcribe", job)
                return
            except ocr_stage.TranscribeOCRError as err:
                fallback_ok = (
                    attempt == 0
                    and run_engine == "ocr"
                    and "Không phát hiện được phụ đề cứng" in str(err)
                    and state.social_link is not None
                )
                if not fallback_ok:
                    logger.exception("{} lỗi {}", run_label, project_id)
                    job.items[0].status = "failed"
                    job.items[0].error = str(err)
                    job.status = "failed"
                    job.error = str(err)
                    with pj.locked_project(project_id) as s:
                        s.stages["transcribe"].status = StageStatus.failed
                        s.stages["transcribe"].error = str(err)
                    pj.append_log(project_id, "transcribe", f"Lỗi: {err}")
                    _maybe_fail_social(project_id, "transcribe", str(err))
                    return
                logger.warning(
                    "_start_transcribe: OCR không thấy phụ đề cứng cho '{}' — tự thử lại bằng Whisper", project_id
                )
                pj.append_log(project_id, "transcribe", f"OCR không thấy phụ đề cứng — tự chuyển sang Whisper: {err}")
                run_engine, run_fn, run_label = _resolve_transcribe_engine("whisper")
                run_extra = {}
                job.items[0] = jobs.JobItem(id=run_engine, label=run_label)
                job.items[0].status = "running"
                job.current_label = run_label
                with pj.locked_project(project_id) as s:
                    s.stages["transcribe"].engine = run_engine
            except Exception as err:
                logger.exception("{} lỗi {}", run_label, project_id)
                job.items[0].status = "failed"
                job.items[0].error = str(err)
                job.status = "failed"
                job.error = str(err)
                with pj.locked_project(project_id) as s:
                    s.stages["transcribe"].status = StageStatus.failed
                    s.stages["transcribe"].error = str(err)
                pj.append_log(project_id, "transcribe", f"Lỗi: {err}")
                _maybe_fail_social(project_id, "transcribe", str(err))
                return

        job.items[0].status = "done"
        job.done_count = job.total
        job.status = "done"
        with pj.locked_project(project_id) as s:
            s.stages["transcribe"].status = StageStatus.done
            s.stages["transcribe"].output = "sub_zh.srt"
            s.stages["transcribe"].progress = f"{len(cues)} câu · {lang}"
            s.stages["transcribe"].error = None
            s.stages["transcribe"].engine = run_engine
            s.stages["transcribe"].at = datetime.now()
        pj.append_log(project_id, "transcribe", f"{len(cues)} câu · {lang}")
        _maybe_chain(project_id, "transcribe")

    return jobs.start_job(f"{project_id}:transcribe", total, target)


@app.post("/api/projects/{project_id}/transcribe", status_code=202)
def start_transcribe_route(project_id: str, body: StartTranscribeRequest | None = None):
    state = pj.load_project(project_id)
    _require_done(state, "ingest", "Chưa upload video")
    if not state.video_relpath:
        raise HTTPException(status_code=409, detail="Chưa có file video")
    engine, _, engine_label = _resolve_transcribe_engine(body.engine if body else None)
    job = _start_transcribe(project_id, engine, body.crop_region if body else None)
    if job is None:
        raise HTTPException(status_code=409, detail=f"{engine_label} đang chạy cho dự án này")
    return {"status": "started"}


def _start_translate(project_id: str) -> jobs.JobState | None:
    root = pj.project_dir(project_id)
    n = len(load_srt(root / "sub_zh.srt")) or 1

    def target(job: jobs.JobState) -> None:
        job.items = [jobs.JobItem(id="gemini", label="Gemini zh→vi")]
        job.items[0].status = "running"
        job.current_label = "Gemini"
        shutil.rmtree(root / "audio", ignore_errors=True)
        with pj.locked_project(project_id) as s:
            pj.reset_from(s, "tts")
            s.stages["translate"].status = StageStatus.running
            s.stages["translate"].error = None

        def on_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            _, _, entity = translate_stage.translate_project(root, on_progress=on_progress)
        except jobs.JobCancelled:
            _mark_cancelled(project_id, "translate", job)
            return
        except (translate_stage.LLMNotConfigured, translate_stage.GeminiQuotaError) as err:
            job.items[0].status = "failed"
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.stages["translate"].status = StageStatus.failed
                s.stages["translate"].error = str(err)
            pj.append_log(project_id, "translate", str(err))
            _maybe_fail_social(project_id, "translate", str(err))
            return
        except Exception as err:
            logger.exception("Gemini lỗi {}", project_id)
            job.items[0].status = "failed"
            job.items[0].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.stages["translate"].status = StageStatus.failed
                s.stages["translate"].error = str(err)
            pj.append_log(project_id, "translate", f"Lỗi: {err}")
            _maybe_fail_social(project_id, "translate", str(err))
            return

        job.items[0].status = "done"
        job.done_count = job.total
        job.status = "done"
        with pj.locked_project(project_id) as s:
            s.stages["translate"].status = StageStatus.done
            s.stages["translate"].output = "sub_vi.srt"
            s.stages["translate"].progress = f"{n} câu · {len(entity)} tên riêng"
            s.stages["translate"].error = None
            s.stages["translate"].at = datetime.now()
        pj.append_log(project_id, "translate", f"{n} câu · {len(entity)} tên riêng")
        _maybe_chain(project_id, "translate")

    return jobs.start_job(f"{project_id}:translate", n, target)


@app.post("/api/projects/{project_id}/translate", status_code=202)
def start_translate_route(project_id: str):
    state = pj.load_project(project_id)
    _require_done(state, "transcribe", "Chưa có phụ đề tiếng Trung — chạy Whisper trước")
    job = _start_translate(project_id)
    if job is None:
        raise HTTPException(status_code=409, detail="Gemini đang chạy cho dự án này")
    return {"status": "started"}


# ------------------------------------------------------------------ Per-cue edit / retranslate / re-TTS


def _cue_response(root: Path, cue_id: int) -> SrtCue:
    zh = next((c for c in load_srt(root / "sub_zh.srt") if c.id == cue_id), None)
    vi = next((c for c in load_srt(root / "sub_vi.srt") if c.id == cue_id), None)
    if zh is None and vi is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy câu #{cue_id}")
    base = zh or vi
    return SrtCue(id=cue_id, start=base.start, end=base.end, text=zh.text if zh else "", text_vi=vi.text if vi else None)


def _reject_if_busy(project_id: str) -> None:
    if jobs.is_job_running(f"{project_id}:translate") or jobs.is_job_running(f"{project_id}:tts"):
        raise HTTPException(status_code=409, detail="Đang có job chạy — đợi xong đã")


@app.patch("/api/projects/{project_id}/cues/{cue_id}", response_model=SrtCue)
def update_cue_route(project_id: str, cue_id: int, body: UpdateCueRequest):
    state = pj.load_project(project_id)
    _require_done(state, "translate", "Chưa có bản dịch — chạy Gemini trước")
    _reject_if_busy(project_id)
    root = pj.project_dir(project_id)
    try:
        update_cue_text(root / "sub_vi.srt", cue_id, body.text_vi)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    pj.append_log(project_id, "translate", f"Sửa tay câu #{cue_id}")
    return _cue_response(root, cue_id)


@app.post("/api/projects/{project_id}/cues/{cue_id}/retranslate", response_model=SrtCue)
def retranslate_cue_route(project_id: str, cue_id: int):
    state = pj.load_project(project_id)
    _require_done(state, "translate", "Chưa có bản dịch — chạy Gemini trước")
    _reject_if_busy(project_id)
    root = pj.project_dir(project_id)
    try:
        text_vi = translate_stage.retranslate_cue(root, cue_id)
    except translate_stage.LLMNotConfigured as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except translate_stage.GeminiQuotaError as err:
        raise HTTPException(status_code=429, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    pj.append_log(project_id, "translate", f"Dịch lại câu #{cue_id}: {text_vi[:60]}")
    return _cue_response(root, cue_id)


@app.post("/api/projects/{project_id}/cues/{cue_id}/tts", response_model=TTSManifestEntryResponse)
def tts_cue_route(project_id: str, cue_id: int, body: TTSCueRequest | None = None):
    state = pj.load_project(project_id)
    _require_done(state, "translate", "Chưa có bản dịch — chạy Gemini trước")
    _reject_if_busy(project_id)
    root = pj.project_dir(project_id)
    voice = body.voice if body and body.voice else ""
    engine = state.stages["tts"].engine or "capcut"
    mod = _tts_module(engine)
    voices = _tts_voices(engine)
    if voice not in {v["id"] for v in voices}:
        voice = mod.DEFAULT_VOICE
    try:
        entry = mod.tts_single_segment(root, cue_id, voice=voice)
    except mod.TTSError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    pj.append_log(project_id, "tts", f"Đọc lại câu #{cue_id}")
    return TTSManifestEntryResponse(**entry)


@app.get("/api/tts/preview")
def tts_preview(voice: str = "", engine: str = "capcut"):
    engine = "vieneu" if engine == "vieneu" else "capcut"
    mod = _tts_module(engine)
    voices = _tts_voices(engine)
    voice = voice if voice in {v["id"] for v in voices} else mod.DEFAULT_VOICE
    try:
        audio_bytes = mod.preview_voice(voice)
    except mod.TTSError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return Response(content=audio_bytes, media_type="audio/mpeg")


def _start_tts(project_id: str, voice: str, retry_only: bool = False, engine: str = "capcut") -> jobs.JobState | None:
    root = pj.project_dir(project_id)
    mod = _tts_module(engine)
    voices = _tts_voices(engine)
    engine_label = _tts_engine_label(engine)

    if voice not in {v["id"] for v in voices}:
        voice = mod.DEFAULT_VOICE
    voice_label = next((v["label"] for v in voices if v["id"] == voice), voice)

    if retry_only:
        manifest_path = root / "audio" / "manifest.json"
        if not manifest_path.exists():
            return None
        prev_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        n = sum(1 for m in prev_manifest if m.get("error")) or 1
    else:
        n = len(load_srt(root / "sub_vi.srt")) or 1

    def target(job: jobs.JobState) -> None:
        label = f"Thử lại câu lỗi · {voice_label}" if retry_only else f"{engine_label} · {voice_label}"
        job.items = [jobs.JobItem(id="tts", label=label)]
        job.items[0].status = "running"
        job.current_label = voice_label
        with pj.locked_project(project_id) as s:
            pj.reset_from(s, "assemble")
            s.stages["tts"].status = StageStatus.running
            s.stages["tts"].error = None

        def on_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            if retry_only:
                _, manifest = mod.retry_failed_segments(root, voice=voice, on_progress=on_progress)
            else:
                _, manifest = mod.tts_project(root, voice=voice, on_progress=on_progress)
        except jobs.JobCancelled:
            _mark_cancelled(project_id, "tts", job)
            return
        except mod.TTSError as err:
            job.items[0].status = "failed"
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.stages["tts"].status = StageStatus.failed
                s.stages["tts"].error = str(err)
            pj.append_log(project_id, "tts", str(err))
            _maybe_fail_social(project_id, "tts", str(err))
            return
        except Exception as err:
            logger.exception("TTS lỗi {}", project_id)
            job.items[0].status = "failed"
            job.items[0].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.stages["tts"].status = StageStatus.failed
                s.stages["tts"].error = str(err)
            pj.append_log(project_id, "tts", f"Lỗi: {err}")
            _maybe_fail_social(project_id, "tts", str(err))
            return

        ok = sum(1 for m in manifest if m.get("path"))
        failed = sum(1 for m in manifest if m.get("error"))
        job.items[0].status = "done"
        job.done_count = job.total
        job.status = "done"
        with pj.locked_project(project_id) as s:
            s.stages["tts"].status = StageStatus.done
            s.stages["tts"].output = "audio/manifest.json"
            s.stages["tts"].progress = f"{ok}/{len(manifest)} câu · {voice_label}" + (f" · {failed} lỗi" if failed else "")
            s.stages["tts"].engine = engine
            s.stages["tts"].error = None
            s.stages["tts"].at = datetime.now()
            s.auto_voice = voice  # nhớ giọng vừa dùng — mặc định cho lần chạy sau
            s.auto_tts_engine = engine
        pj.append_log(project_id, "tts", f"{ok}/{len(manifest)} câu · {voice_label}")
        _maybe_chain(project_id, "tts")

    return jobs.start_job(f"{project_id}:tts", n, target)


@app.post("/api/projects/{project_id}/tts", status_code=202)
def start_tts_route(project_id: str, body: StartTTSRequest | None = None):
    state = pj.load_project(project_id)
    _require_done(state, "translate", "Chưa có bản dịch tiếng Việt — chạy Gemini trước")
    voice = body.voice if body and body.voice else ""
    retry_only = bool(body and body.retry_failed_only)
    # retry chỉ tạo lại câu lỗi trong manifest ĐÃ có — phải dùng đúng engine
    # của lần chạy trước, bỏ qua engine truyền vào (tránh trộn giọng 2 engine
    # khác namespace trong cùng 1 manifest).
    engine = (state.stages["tts"].engine or "capcut") if retry_only else ("vieneu" if body and body.engine == "vieneu" else "capcut")
    if retry_only:
        manifest_path = pj.project_dir(project_id) / "audio" / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=409, detail="Chưa chạy TTS lần nào")
    job = _start_tts(project_id, voice, retry_only=retry_only, engine=engine)
    if job is None:
        raise HTTPException(status_code=409, detail="TTS đang chạy cho dự án này")
    return {"status": "started"}


# ------------------------------------------------------------------ Assemble (CapCut draft)


def _start_assemble_multi(
    project_id: str,
    state,
    audio_mode: str,
    min_video_speed: float = 0.85,
    original_audio_volume_db: float = -13.0,
) -> jobs.JobState | None:
    """Ráp TOÀN BỘ episode của dự án dài tập vào 1 draft CapCut duy nhất —
    gọi lại mỗi khi 1 tập mới xong `tts` (xem `_maybe_chain_episode`) hoặc khi
    người dùng bấm tay; luôn build lại draft từ đầu theo danh sách episode
    hiện có (rẻ vì chỉ dựng track, không chạy lại whisper/gemini/tts)."""
    episodes = sorted(state.episodes, key=lambda e: e.order)
    if not episodes or any(not ep.video_relpath for ep in episodes):
        return None
    mute_original_audio = audio_mode == "mute"
    bg_filename = "background.wav" if audio_mode == "separated" else "background_original.wav"

    def target(job: jobs.JobState) -> None:
        bg_label = "Tách nhạc nền" if audio_mode == "separated" else "Trích audio gốc"
        bg_items = [] if mute_original_audio else [
            jobs.JobItem(id=f"background:{ep.episode_id}", label=f"{bg_label} (tập {i + 1})")
            for i, ep in enumerate(episodes)
        ]
        job.items = bg_items + [jobs.JobItem(id="draft", label="Dựng draft CapCut")]
        job.current_label = job.items[0].label
        with pj.locked_project(project_id) as s:
            s.stages["assemble"].status = StageStatus.running
            s.stages["assemble"].error = None

        def on_bg_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.current_label = label

        sources: list[assemble_stage.EpisodeSource] = []
        for i, ep in enumerate(episodes):
            ep_root = pj.episode_dir(project_id, ep.episode_id)
            video_path = ep_root / ep.video_relpath
            bg_path: Path | None = ep_root / bg_filename

            if mute_original_audio:
                bg_path = None
            elif not bg_path.exists():
                bg_items[i].status = "running"
                try:
                    if audio_mode == "separated":
                        dub_audio_stage.extract_background(video_path, bg_path, on_progress=on_bg_progress, job=job)
                    else:
                        dub_audio_stage.extract_original_audio(video_path, bg_path, on_progress=on_bg_progress)
                except jobs.JobCancelled:
                    _mark_cancelled(project_id, "assemble", job)
                    return
                except dub_audio_stage.DubAudioError as err:
                    bg_items[i].status = "failed"
                    job.status = "failed"
                    job.error = str(err)
                    with pj.locked_project(project_id) as s:
                        s.stages["assemble"].status = StageStatus.failed
                        s.stages["assemble"].error = str(err)
                    pj.append_log(project_id, "assemble", f"[{ep.episode_id}] {err}")
                    return
                bg_items[i].status = "done"
            else:
                bg_items[i].status = "done"

            manifest_path = ep_root / "audio" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
            vi_cues = load_srt(ep_root / "sub_vi.srt")
            sources.append(
                assemble_stage.EpisodeSource(
                    video_path=video_path,
                    background_path=bg_path,
                    manifest=manifest,
                    vi_cues=vi_cues,
                    audio_dir=ep_root / "audio",
                )
            )

        job.items[-1].status = "running"
        job.current_label = "Dựng draft"

        def on_draft_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            draft_path = assemble_stage.assemble_multi(
                sources,
                draft_name=project_id,
                on_progress=on_draft_progress,
                mute_original_audio=mute_original_audio,
                min_video_speed=min_video_speed,
                background_volume=_resolve_background_volume_linear(audio_mode, original_audio_volume_db),
            )
        except jobs.JobCancelled:
            _mark_cancelled(project_id, "assemble", job)
            return
        except assemble_stage.AssembleError as err:
            job.items[-1].status = "failed"
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.stages["assemble"].status = StageStatus.failed
                s.stages["assemble"].error = str(err)
            pj.append_log(project_id, "assemble", str(err))
            return
        except Exception as err:
            logger.exception("Assemble (multi) lỗi {}", project_id)
            job.items[-1].status = "failed"
            job.items[-1].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.stages["assemble"].status = StageStatus.failed
                s.stages["assemble"].error = str(err)
            pj.append_log(project_id, "assemble", f"Lỗi: {err}")
            return

        job.items[-1].status = "done"
        job.status = "done"
        with pj.locked_project(project_id) as s:
            s.stages["assemble"].status = StageStatus.done
            s.stages["assemble"].output = str(draft_path)
            s.stages["assemble"].progress = f"draft: {project_id} · {len(episodes)} tập"
            s.stages["assemble"].error = None
            s.stages["assemble"].at = datetime.now()
            s.auto_audio_mode = audio_mode
            s.auto_min_video_speed = min_video_speed
            s.auto_original_audio_volume_db = original_audio_volume_db
        pj.append_log(project_id, "assemble", f"draft → {draft_path} ({len(episodes)} tập)")

    return jobs.start_job(f"{project_id}:assemble", 1, target)


def _resolve_background_volume_linear(audio_mode: str, original_audio_volume_db: float) -> float:
    """Quy đổi dB sang hệ số tuyến tính CapCut dùng cho track nhạc nền — chỉ
    áp dụng mức tuỳ chỉnh khi audio_mode="original" (âm thanh gốc giữ
    nguyên), các mode khác giữ mức mặc định cũ (assemble_stage.BACKGROUND_VOLUME)."""
    if audio_mode != "original":
        return assemble_stage.BACKGROUND_VOLUME
    return 10 ** (original_audio_volume_db / 20)


def _start_assemble(
    project_id: str,
    audio_mode: str = "original",
    min_video_speed: float = 0.85,
    original_audio_volume_db: float = -13.0,
) -> jobs.JobState | None:
    state = pj.load_project(project_id)
    root = pj.project_dir(project_id)

    if state.project_type == "multi":
        return _start_assemble_multi(project_id, state, audio_mode, min_video_speed, original_audio_volume_db)

    if not state.video_relpath:
        return None

    mute_original_audio = audio_mode == "mute"
    bg_filename = "background.wav" if audio_mode == "separated" else "background_original.wav"

    def target(job: jobs.JobState) -> None:
        if mute_original_audio:
            bg_label = "Tắt âm thanh gốc"
        elif audio_mode == "separated":
            bg_label = "Tách nhạc nền (demucs)"
        else:
            bg_label = "Trích audio gốc (giữ nguyên)"
        job.items = [
            jobs.JobItem(id="background", label=bg_label),
            jobs.JobItem(id="draft", label="Dựng draft CapCut"),
        ]
        job.current_label = job.items[0].label
        with pj.locked_project(project_id) as s:
            s.stages["assemble"].status = StageStatus.running
            s.stages["assemble"].error = None

        video_path = root / state.video_relpath
        bg_path = root / bg_filename

        def on_bg_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.current_label = label

        if mute_original_audio:
            bg_path = None
        elif not bg_path.exists():
            job.items[0].status = "running"
            try:
                if audio_mode == "separated":
                    dub_audio_stage.extract_background(video_path, bg_path, on_progress=on_bg_progress, job=job)
                else:
                    dub_audio_stage.extract_original_audio(video_path, bg_path, on_progress=on_bg_progress)
            except jobs.JobCancelled:
                _mark_cancelled(project_id, "assemble", job)
                return
            except dub_audio_stage.DubAudioError as err:
                job.items[0].status = "failed"
                job.status = "failed"
                job.error = str(err)
                with pj.locked_project(project_id) as s:
                    s.stages["assemble"].status = StageStatus.failed
                    s.stages["assemble"].error = str(err)
                pj.append_log(project_id, "assemble", str(err))
                _maybe_fail_social(project_id, "assemble", str(err))
                return
        job.items[0].status = "done"

        job.items[1].status = "running"
        job.current_label = "Dựng draft"

        def on_draft_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            manifest_path = root / "audio" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
            vi_cues = load_srt(root / "sub_vi.srt")
            draft_path = assemble_stage.assemble_project(
                video_path=video_path,
                background_path=bg_path,
                manifest=manifest,
                vi_cues=vi_cues,
                audio_dir=root / "audio",
                draft_name=project_id,
                on_progress=on_draft_progress,
                mute_original_audio=mute_original_audio,
                min_video_speed=min_video_speed,
                background_volume=_resolve_background_volume_linear(audio_mode, original_audio_volume_db),
            )
        except jobs.JobCancelled:
            _mark_cancelled(project_id, "assemble", job)
            return
        except assemble_stage.AssembleError as err:
            job.items[1].status = "failed"
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.stages["assemble"].status = StageStatus.failed
                s.stages["assemble"].error = str(err)
            pj.append_log(project_id, "assemble", str(err))
            _maybe_fail_social(project_id, "assemble", str(err))
            return
        except Exception as err:
            logger.exception("Assemble lỗi {}", project_id)
            job.items[1].status = "failed"
            job.items[1].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.stages["assemble"].status = StageStatus.failed
                s.stages["assemble"].error = str(err)
            pj.append_log(project_id, "assemble", f"Lỗi: {err}")
            _maybe_fail_social(project_id, "assemble", str(err))
            return

        job.items[1].status = "done"
        job.status = "done"
        with pj.locked_project(project_id) as s:
            s.stages["assemble"].status = StageStatus.done
            s.stages["assemble"].output = str(draft_path)
            s.stages["assemble"].progress = f"draft: {project_id}"
            s.stages["assemble"].error = None
            s.stages["assemble"].at = datetime.now()
            s.auto_audio_mode = audio_mode
            s.auto_min_video_speed = min_video_speed
            s.auto_original_audio_volume_db = original_audio_volume_db
        pj.append_log(project_id, "assemble", f"draft → {draft_path}")
        _maybe_chain_social(project_id, "assemble")

    return jobs.start_job(f"{project_id}:assemble", 1, target)


# ------------------------------------------------------------------ Xuất video trực tiếp (ffmpeg, không qua CapCut)
# Hành động PHỤ song song với assemble/CapCut — xem comment ở
# `ProjectState.export` (models.py) lý do không nằm trong STAGE_ORDER.


def _mark_export_cancelled(project_id: str, job: jobs.JobState) -> None:
    """Mirror `_mark_cancelled` nhưng ghi vào `state.export` — `export` nằm
    RIÊNG ngoài `stages` dict (xem models.py) nên không dùng chung được."""
    job.status = "cancelled"
    for it in job.items:
        if it.status == "running":
            it.status = "failed"
            it.error = "Đã dừng"
    with pj.locked_project(project_id) as s:
        s.export.status = StageStatus.failed
        s.export.error = "Đã dừng theo yêu cầu người dùng"
    pj.append_log(project_id, "export", "Đã dừng theo yêu cầu người dùng")
    _maybe_fail_social(project_id, "export", "Đã dừng theo yêu cầu người dùng")


def _export_dir(project_id: str) -> Path:
    d = pj.project_dir(project_id) / "export"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _export_asset_path(project_id: str, stem: str) -> Path | None:
    """Tìm file `music.*`/`logo.*` đã upload trong thư mục export của
    project (đuôi file có thể khác nhau tuỳ định dạng người dùng upload)."""
    matches = sorted(_export_dir(project_id).glob(f"{stem}.*"))
    return matches[0] if matches else None


def _social_asset_path(social_id: str, stem: str) -> Path | None:
    """Mirror `_export_asset_path` nhưng ở cấp "Dự án tự động" — 1 file
    logo/nhạc nền DÙNG CHUNG cho mọi video của dự án đó (khác project đơn,
    mỗi lần kích hoạt tạo project mới hoàn toàn nên không có chỗ nào bền để
    lưu file riêng theo từng video)."""
    matches = sorted(sp.social_dir(social_id).glob(f"{stem}.*"))
    return matches[0] if matches else None


def _start_export(
    project_id: str,
    audio_mode: str,
    min_video_speed: float,
    original_audio_volume_db: float = -13.0,
    subtitle_font_size: int = 6,
) -> jobs.JobState | None:
    state = pj.load_project(project_id)
    root = pj.project_dir(project_id)
    if not state.video_relpath:
        return None

    mute_original_audio = audio_mode == "mute"
    bg_filename = "background.wav" if audio_mode == "separated" else "background_original.wav"

    def target(job: jobs.JobState) -> None:
        bg_label = (
            "Tắt âm thanh gốc" if mute_original_audio
            else "Tách nhạc nền (demucs)" if audio_mode == "separated"
            else "Trích audio gốc (giữ nguyên)"
        )
        job.items = [jobs.JobItem(id="background", label=bg_label), jobs.JobItem(id="render", label="Dựng video")]
        job.current_label = job.items[0].label
        with pj.locked_project(project_id) as s:
            s.export.status = StageStatus.running
            s.export.error = None

        video_path = root / state.video_relpath
        bg_path: Path | None = root / bg_filename

        def on_bg_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.current_label = label

        if mute_original_audio:
            bg_path = None
        elif not bg_path.exists():
            job.items[0].status = "running"
            try:
                if audio_mode == "separated":
                    dub_audio_stage.extract_background(video_path, bg_path, on_progress=on_bg_progress, job=job)
                else:
                    dub_audio_stage.extract_original_audio(video_path, bg_path, on_progress=on_bg_progress)
            except jobs.JobCancelled:
                _mark_export_cancelled(project_id, job)
                return
            except dub_audio_stage.DubAudioError as err:
                job.items[0].status = "failed"
                job.status = "failed"
                job.error = str(err)
                with pj.locked_project(project_id) as s:
                    s.export.status = StageStatus.failed
                    s.export.error = str(err)
                pj.append_log(project_id, "export", str(err))
                _maybe_fail_social(project_id, "export", str(err))
                return
        job.items[0].status = "done"

        job.items[1].status = "running"
        job.current_label = "Dựng video"

        def on_render_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        blur_active_ranges_s: list[tuple[float, float]] | None = None
        if state.export_blur_region:
            blur_region = tuple(state.export_blur_region)
            # Có sẵn từ lượt dò SỚM (`_start_early_blur_detect`, chạy ngay
            # sau ingest) — tái dùng luôn, không dò lại. [] nghĩa là lượt dò
            # sớm không tìm được khoảng nào → che suốt video (None).
            if state.export_blur_active_ranges:
                blur_active_ranges_s = [tuple(r) for r in state.export_blur_active_ranges]
        else:
            # Mặc định tự động dò vùng phụ đề cũ bằng OCR khi chưa khoanh tay
            # — người dùng chốt: bật mặc định cho hầu hết video có phụ đề
            # cứng nằm gần đáy khung hình (xem detect_subtitle_region). Rơi
            # vào nhánh này khi lượt dò SỚM chưa kịp xong lúc export bắt đầu
            # (pipeline chạy nhanh hơn OCR) hoặc chưa từng chạy (project cũ).
            job.current_label = "Tự dò vùng phụ đề cũ (OCR)"
            search_region = tuple(state.auto_ocr_crop_region) if state.auto_ocr_crop_region else None
            blur_region, ranges = _detect_blur_region_and_ranges(video_path, search_region=search_region)
            if blur_region is not None:
                with pj.locked_project(project_id) as s:
                    s.export_blur_region = list(blur_region)
                    s.export_blur_active_ranges = [list(r) for r in ranges] if ranges else None
                blur_active_ranges_s = ranges or None

        music_path = _export_asset_path(project_id, "music")
        logo_path = _export_asset_path(project_id, "logo")
        if state.social_link is not None:
            # Project do "Dự án tự động" sinh ra — mỗi lần kích hoạt tạo
            # project MỚI hoàn toàn nên không có sẵn music.*/logo.* riêng như
            # project tạo tay upload qua UI — dùng file logo/nhạc nền đã
            # upload Ở CẤP DỰ ÁN TỰ ĐỘNG (`<social_dir>/logo.*`,
            # `<social_dir>/music.*`) làm chung cho MỌI video của dự án đó.
            if music_path is None:
                music_path = _social_asset_path(state.social_link.social_id, "music")
            if logo_path is None:
                logo_path = _social_asset_path(state.social_link.social_id, "logo")
        output_path = _export_dir(project_id) / "final.mp4"
        background_volume_db = original_audio_volume_db if audio_mode == "original" else None
        music_volume_db = state.auto_music_volume_db

        try:
            manifest_path = root / "audio" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
            vi_cues = load_srt(root / "sub_vi.srt")
            export_direct_stage.render_video(
                video_path=video_path,
                background_path=bg_path,
                manifest=manifest,
                vi_cues=vi_cues,
                audio_dir=root / "audio",
                output_path=output_path,
                blur_region=blur_region,
                blur_active_ranges_s=blur_active_ranges_s,
                music_path=music_path,
                logo_path=logo_path,
                min_video_speed=min_video_speed,
                background_volume_db=background_volume_db,
                music_volume_db=music_volume_db,
                subtitle_font_size=subtitle_font_size,
                on_progress=on_render_progress,
                job=job,
            )
        except jobs.JobCancelled:
            _mark_export_cancelled(project_id, job)
            return
        except export_direct_stage.ExportDirectError as err:
            job.items[1].status = "failed"
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.export.status = StageStatus.failed
                s.export.error = str(err)
            pj.append_log(project_id, "export", str(err))
            _maybe_fail_social(project_id, "export", str(err))
            return
        except Exception as err:
            logger.exception("Xuất video trực tiếp lỗi {}", project_id)
            job.items[1].status = "failed"
            job.items[1].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.export.status = StageStatus.failed
                s.export.error = str(err)
            pj.append_log(project_id, "export", f"Lỗi: {err}")
            _maybe_fail_social(project_id, "export", str(err))
            return

        job.items[1].status = "done"
        job.status = "done"
        with pj.locked_project(project_id) as s:
            s.export.status = StageStatus.done
            s.export.output = str(output_path)
            s.export.progress = "final.mp4"
            s.export.error = None
            s.export.at = datetime.now()
        pj.append_log(project_id, "export", f"video → {output_path}")
        _maybe_chain_social(project_id, "export")

    return jobs.start_job(f"{project_id}:export", 1, target)


@app.post("/api/projects/{project_id}/export", status_code=202)
def start_export_route(project_id: str, body: StartExportRequest | None = None):
    state = pj.load_project(project_id)
    if state.project_type == "multi" or state.split_mode:
        raise HTTPException(status_code=409, detail="Xuất video trực tiếp hiện chỉ hỗ trợ dự án đơn")
    _require_done(state, "tts", "Chưa có audio TTS — chạy TTS trước")
    if not state.video_relpath:
        raise HTTPException(status_code=409, detail="Chưa có file video")
    job = _start_export(
        project_id,
        audio_mode=(body.audio_mode if body else "original"),
        min_video_speed=(body.min_video_speed if body else 0.85),
        original_audio_volume_db=(body.original_audio_volume_db if body else -13.0),
        subtitle_font_size=(body.subtitle_font_size if body else 6),
    )
    if job is None:
        raise HTTPException(status_code=409, detail="Xuất video đang chạy cho dự án này")
    return {"status": "started"}


@app.post("/api/projects/{project_id}/export/blur-region")
def update_export_blur_region(project_id: str, body: UpdateExportBlurRegionRequest):
    with pj.locked_project(project_id) as s:
        s.export_blur_region = body.region
    return {"status": "ok"}


@app.post("/api/projects/{project_id}/export/detect-blur-region")
def detect_export_blur_region(project_id: str):
    state = pj.load_project(project_id)
    if not state.video_relpath:
        raise HTTPException(status_code=409, detail="Chưa có file video")
    video_path = pj.project_dir(project_id) / state.video_relpath
    try:
        region = ocr_stage.detect_subtitle_region(video_path)
    except ocr_stage.TranscribeOCRError as err:
        raise HTTPException(status_code=500, detail=str(err)) from err
    if region is None:
        raise HTTPException(status_code=422, detail="Không phát hiện được chữ phụ đề nào trong video")
    with pj.locked_project(project_id) as s:
        s.export_blur_region = list(region)
    return {"status": "ok", "region": list(region)}


@app.post("/api/projects/{project_id}/export/reveal")
def reveal_export_video(project_id: str):
    video_path = _export_dir(project_id) / "final.mp4"
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Chưa xuất video hoặc file không tồn tại trên đĩa")
    try:
        subprocess.run(["explorer", f"/select,{video_path}"], check=False)
    except FileNotFoundError as err:
        raise HTTPException(status_code=500, detail="Chỉ hỗ trợ mở Explorer trên Windows") from err
    return {"status": "ok"}


@app.post("/api/projects/{project_id}/export/music")
async def upload_export_music(project_id: str, file: UploadFile = File(...)):
    ext = Path(file.filename or "music.mp3").suffix or ".mp3"
    d = _export_dir(project_id)
    for old in d.glob("music.*"):
        old.unlink(missing_ok=True)
    dest = d / f"music{ext}"
    dest.write_bytes(await file.read())
    return {"status": "ok"}


@app.delete("/api/projects/{project_id}/export/music")
def delete_export_music(project_id: str):
    for old in _export_dir(project_id).glob("music.*"):
        old.unlink(missing_ok=True)
    return {"status": "ok"}


@app.post("/api/projects/{project_id}/export/logo")
async def upload_export_logo(project_id: str, file: UploadFile = File(...)):
    ext = Path(file.filename or "logo.png").suffix or ".png"
    d = _export_dir(project_id)
    for old in d.glob("logo.*"):
        old.unlink(missing_ok=True)
    dest = d / f"logo{ext}"
    dest.write_bytes(await file.read())
    return {"status": "ok"}


@app.delete("/api/projects/{project_id}/export/logo")
def delete_export_logo(project_id: str):
    for old in _export_dir(project_id).glob("logo.*"):
        old.unlink(missing_ok=True)
    return {"status": "ok"}


@app.get("/api/projects/{project_id}/export/music")
def get_export_music(project_id: str):
    # Đuôi file thật do người dùng upload quyết định (`music.mp3`/`.wav`/...)
    # — route riêng này phục vụ đúng file bất kể đuôi, để FE khỏi phải đoán.
    path = _export_asset_path(project_id, "music")
    if not path:
        raise HTTPException(status_code=404, detail="Chưa có nhạc nền")
    return FileResponse(path)


@app.get("/api/projects/{project_id}/export/logo")
def get_export_logo(project_id: str):
    path = _export_asset_path(project_id, "logo")
    if not path:
        raise HTTPException(status_code=404, detail="Chưa có logo")
    return FileResponse(path)


# --- Logo/nhạc nền dùng chung cho "Dự án tự động" (mirror export/music,logo ở trên) ---


@app.post("/api/social/{social_id}/music")
async def upload_social_music(social_id: str, file: UploadFile = File(...)):
    ext = Path(file.filename or "music.mp3").suffix or ".mp3"
    d = sp.social_dir(social_id)
    for old in d.glob("music.*"):
        old.unlink(missing_ok=True)
    dest = d / f"music{ext}"
    dest.write_bytes(await file.read())
    return {"status": "ok"}


@app.delete("/api/social/{social_id}/music")
def delete_social_music(social_id: str):
    for old in sp.social_dir(social_id).glob("music.*"):
        old.unlink(missing_ok=True)
    return {"status": "ok"}


@app.get("/api/social/{social_id}/music")
def get_social_music(social_id: str):
    path = _social_asset_path(social_id, "music")
    if not path:
        raise HTTPException(status_code=404, detail="Chưa có nhạc nền")
    return FileResponse(path)


@app.post("/api/social/{social_id}/logo")
async def upload_social_logo(social_id: str, file: UploadFile = File(...)):
    ext = Path(file.filename or "logo.png").suffix or ".png"
    d = sp.social_dir(social_id)
    for old in d.glob("logo.*"):
        old.unlink(missing_ok=True)
    dest = d / f"logo{ext}"
    dest.write_bytes(await file.read())
    return {"status": "ok"}


@app.delete("/api/social/{social_id}/logo")
def delete_social_logo(social_id: str):
    for old in sp.social_dir(social_id).glob("logo.*"):
        old.unlink(missing_ok=True)
    return {"status": "ok"}


@app.get("/api/social/{social_id}/logo")
def get_social_logo(social_id: str):
    path = _social_asset_path(social_id, "logo")
    if not path:
        raise HTTPException(status_code=404, detail="Chưa có logo")
    return FileResponse(path)


@app.post("/api/projects/{project_id}/assemble", status_code=202)
def start_assemble_route(project_id: str, body: StartAssembleRequest | None = None):
    state = pj.load_project(project_id)
    if state.split_mode:
        raise HTTPException(
            status_code=409, detail="Dự án đã chia đoạn — ráp draft riêng từng đoạn ở mục Các đoạn, không dùng nút này"
        )
    if state.project_type == "multi":
        if not pj.all_episodes_stage_done(state, "tts"):
            raise HTTPException(status_code=409, detail="Chưa có tập nào xong TTS — chạy hết các tập trước")
    else:
        _require_done(state, "tts", "Chưa có audio TTS — chạy TTS trước")
        if not state.video_relpath:
            raise HTTPException(status_code=409, detail="Chưa có file video")
    job = _start_assemble(
        project_id,
        audio_mode=(body.audio_mode if body else "original"),
        min_video_speed=(body.min_video_speed if body else 0.85),
        original_audio_volume_db=(body.original_audio_volume_db if body else -13.0),
    )
    if job is None:
        raise HTTPException(status_code=409, detail="Assemble đang chạy cho dự án này")
    return {"status": "started"}


_JOB_STAGES = ("ingest", "transcribe", "translate", "tts", "assemble", "export")


@app.post("/api/projects/{project_id}/jobs/{stage}/cancel")
def cancel_job_route(project_id: str, stage: str):
    if stage not in _JOB_STAGES:
        raise HTTPException(status_code=404, detail="Stage không hỗ trợ job")
    ok = jobs.request_cancel(f"{project_id}:{stage}")
    if not ok:
        raise HTTPException(status_code=409, detail="Không có job nào đang chạy cho stage này")
    return {"status": "cancelling"}


@app.get("/api/projects/{project_id}/jobs/{stage}", response_model=JobStatusResponse)
def job_status(project_id: str, stage: str):
    if stage not in _JOB_STAGES:
        raise HTTPException(status_code=404, detail="Stage không hỗ trợ job")
    job = jobs.get_job(f"{project_id}:{stage}")
    # Job "chết" giữa chừng do 1 exception lọt ra khỏi `target()` (không bắt
    # đúng) để lại đúng triệu chứng như job "mồ côi" do server restart —
    # thread đã chết (`is_alive() == False`) nhưng vẫn còn nằm trong
    # `jobs._jobs` với `status="running"` treo mãi. Coi cả 2 trường hợp này
    # là "mồ côi" như nhau — đã xác nhận thật 1 lần (job đứng yên 0/97 nhiều
    # phút, CPU gần như không dùng, `request_cancel` báo "không có job đang
    # chạy" vì `is_alive()` đã False).
    if job is not None and not job.is_alive():
        job = None
    if job is None:
        state = pj.load_project(project_id)
        # "export" nằm riêng ngoài `stages` dict (xem models.py) — đọc/ghi
        # qua `state.export` thay vì `state.stages["export"]`.
        record = state.export if stage == "export" else state.stages[stage]
        orphaned = record.status == StageStatus.running
        if orphaned:
            # Job chết theo tiến trình cũ (server restart) không kịp ghi lại
            # state — "running" sẽ treo mãi trong project.json nếu không tự
            # sửa ở đây, khiến nút "Chạy ..." cứ hiện đang chạy dù job đã chết.
            with pj.locked_project(project_id) as s:
                s_record = s.export if stage == "export" else s.stages[stage]
                if s_record.status == StageStatus.running:
                    s_record.status = StageStatus.failed
                    s_record.error = "Phiên trước bị gián đoạn (server restart) — bấm chạy lại."
            pj.append_log(project_id, stage, "Phiên trước bị gián đoạn (server restart)")
        return JobStatusResponse(registered=False, orphaned=orphaned)
    return JobStatusResponse(
        registered=True,
        status=job.status,
        total=job.total,
        done_count=job.done_count,
        current_label=job.current_label,
        items=[JobItemResponse(id=it.id, label=it.label, status=it.status, error=it.error) for it in job.items],
        error=job.error,
        started_at=job.started_at,
    )


# ------------------------------------------------------------------ Ghép video (không thuộc project nào)


@app.get("/api/merges")
def list_merges_route():
    return mg.list_merges()


@app.post("/api/merges", status_code=202)
async def create_merge_route(title: str = Form(""), files: list[UploadFile] = File(...)):
    if len(files) < 2:
        raise HTTPException(status_code=400, detail="Cần ít nhất 2 video để ghép")

    filenames = [f.filename or f"video_{i}.mp4" for i, f in enumerate(files)]
    meta = mg.create_merge(title, filenames)
    merge_id = meta["merge_id"]
    root = mg.merge_dir(merge_id)

    input_paths: list[Path] = []
    for i, f in enumerate(files):
        ext = Path(f.filename or "").suffix or ".mp4"
        dest = root / f"input_{i:03d}{ext}"
        with dest.open("wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        input_paths.append(dest)

    def target(job: jobs.JobState) -> None:
        job.items = [jobs.JobItem(id="merge", label="Ghép video")]
        job.items[0].status = "running"
        job.current_label = "Đang ghép video..."
        meta_run = mg.load_meta(merge_id)
        meta_run["status"] = "running"
        mg.save_meta(merge_id, meta_run)

        try:
            out_path = root / "merged.mp4"
            video_merge_stage.merge_videos(input_paths, out_path, job=job)
        except jobs.JobCancelled:
            job.status = "cancelled"
            job.items[0].status = "failed"
            job.items[0].error = "Đã dừng"
            meta_c = mg.load_meta(merge_id)
            meta_c["status"] = "failed"
            meta_c["error"] = "Đã dừng theo yêu cầu người dùng"
            mg.save_meta(merge_id, meta_c)
            return
        except video_merge_stage.VideoMergeError as err:
            job.items[0].status = "failed"
            job.status = "failed"
            job.error = str(err)
            meta_err = mg.load_meta(merge_id)
            meta_err["status"] = "failed"
            meta_err["error"] = str(err)
            mg.save_meta(merge_id, meta_err)
            return
        except Exception as err:
            logger.exception("Ghép video lỗi {}", merge_id)
            job.items[0].status = "failed"
            job.status = "failed"
            job.error = str(err)
            meta_err2 = mg.load_meta(merge_id)
            meta_err2["status"] = "failed"
            meta_err2["error"] = str(err)
            mg.save_meta(merge_id, meta_err2)
            return

        job.items[0].status = "done"
        job.done_count = 1
        job.status = "done"
        meta_done = mg.load_meta(merge_id)
        meta_done["status"] = "done"
        meta_done["output_filename"] = "merged.mp4"
        mg.save_meta(merge_id, meta_done)

    jobs.start_job(f"merge:{merge_id}", 1, target)
    return {"merge_id": merge_id, "status": "started"}


@app.get("/api/merges/{merge_id}/jobs/status", response_model=JobStatusResponse)
def merge_job_status_route(merge_id: str):
    job = jobs.get_job(f"merge:{merge_id}")
    if job is None:
        try:
            meta = mg.load_meta(merge_id)
        except FileNotFoundError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        orphaned = meta.get("status") == "running"
        if orphaned:
            meta["status"] = "failed"
            meta["error"] = "Phiên trước bị gián đoạn (server restart) — bấm ghép lại."
            mg.save_meta(merge_id, meta)
        return JobStatusResponse(registered=False, orphaned=orphaned)
    return JobStatusResponse(
        registered=True,
        status=job.status,
        total=job.total,
        done_count=job.done_count,
        current_label=job.current_label,
        items=[JobItemResponse(id=it.id, label=it.label, status=it.status, error=it.error) for it in job.items],
        error=job.error,
        started_at=job.started_at,
    )


@app.post("/api/merges/{merge_id}/jobs/cancel")
def cancel_merge_job_route(merge_id: str):
    ok = jobs.request_cancel(f"merge:{merge_id}")
    if not ok:
        raise HTTPException(status_code=409, detail="Không có job nào đang chạy")
    return {"status": "cancelling"}


@app.get("/api/merges/{merge_id}/download")
def download_merge_route(merge_id: str):
    try:
        meta = mg.load_meta(merge_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    if meta.get("status") != "done" or not meta.get("output_filename"):
        raise HTTPException(status_code=409, detail="Chưa ghép xong")
    path = mg.merge_dir(merge_id) / meta["output_filename"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy file đã ghép")
    safe_name = pj.slugify(meta.get("title") or merge_id) + ".mp4"
    return FileResponse(path, filename=safe_name, media_type="video/mp4")


@app.delete("/api/merges/{merge_id}", status_code=204)
def delete_merge_route(merge_id: str):
    if jobs.is_job_running(f"merge:{merge_id}"):
        raise HTTPException(status_code=409, detail="Đang ghép — đợi xong hoặc bấm Dừng trước")
    try:
        mg.delete_merge(merge_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


# ------------------------------------------------------------------ Tải video riêng (không thuộc project nào)


@app.post("/api/downloads/pick-folder")
def pick_download_folder_route():
    """Bật hộp thoại chọn thư mục gốc Windows trên chính máy chạy server —
    browser không cho JS lấy đường dẫn ổ đĩa thật từ input chọn thư mục vì lý
    do sandbox, nhưng app này chạy local nên server tự mở dialog thay được.
    Route SYNC (không async) — Starlette tự chạy trong threadpool riêng nên
    không chặn các request khác dù dialog treo chờ người dùng chọn."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$owner = New-Object System.Windows.Forms.Form; $owner.TopMost = $true;"
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$f.Description = 'Chon thu muc luu video';"
        "if ($f.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
        "$owner.Dispose()"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError as err:
        raise HTTPException(status_code=500, detail="Chỉ hỗ trợ chọn thư mục trên Windows") from err
    except subprocess.TimeoutExpired:
        return {"path": None}
    path = (result.stdout or "").strip()
    return {"path": path or None}


@app.get("/api/downloads")
def list_downloads_route():
    return dl.list_downloads()


@app.post("/api/downloads", status_code=202)
def create_download_route(body: CreateDownloadRequest):
    if body.mode == "single":
        if not body.url.strip():
            raise HTTPException(status_code=400, detail="Chưa dán link")
    elif body.mode == "profile":
        if not body.url.strip():
            raise HTTPException(status_code=400, detail="Chưa dán link trang cá nhân")
        if not body.modes:
            raise HTTPException(status_code=400, detail="Chưa chọn chế độ tải (post/like/mix/music)")
    elif body.mode == "search":
        if not body.keyword.strip():
            raise HTTPException(status_code=400, detail="Chưa nhập từ khoá")
    elif body.mode == "info":
        if not body.url.strip():
            raise HTTPException(status_code=400, detail="Chưa dán link trang cá nhân")
        if not body.modes:
            raise HTTPException(status_code=400, detail="Chưa chọn chế độ lấy (post/like/mix/music)")
    else:
        raise HTTPException(status_code=400, detail=f'Mode "{body.mode}" không hợp lệ')
    if not config.douyin_dl_available():
        raise HTTPException(status_code=409, detail="Chưa cấu hình DOUYIN_DL_DIR — xem README để clone douyin-downloader")
    # Chạy 2 lượt douyin-downloader ĐỒNG THỜI (mỗi lượt tự mở 1 trình duyệt
    # headless riêng lấy token) đã xác nhận thật làm Douyin trả về thiếu/rỗng
    # kết quả dù tiến trình báo "thành công" — giới hạn CHẠY 1 LƯỢT TẠI 1 THỜI
    # ĐIỂM cho cả trang, không chỉ theo từng download_id riêng.
    if jobs.any_job_running("download:"):
        raise HTTPException(
            status_code=409,
            detail="Đang có 1 lượt tải/quét khác chạy — đợi xong rồi thử lại (chạy đồng thời dễ khiến Douyin trả kết quả thiếu)",
        )

    title = body.title.strip() or (body.url.strip() or body.keyword.strip())
    meta = dl.create_download(title, body.mode, body.model_dump(exclude={"title"}))
    download_id = meta["download_id"]
    root = dl.download_dir(download_id)
    # Thư mục lưu thật — người dùng chọn (`dest_dir`) hoặc mặc định trong
    # workspace/downloads/<id>/files nếu để trống.
    output_dir = Path(body.dest_dir).expanduser().resolve() if body.dest_dir.strip() else (root / "files")
    meta["output_dir"] = str(output_dir)
    dl.save_meta(download_id, meta)

    # Ước lượng tổng số video để tính % tiến trình thật (JobProgressBar) —
    # không biết trước chính xác (vd trang cá nhân có ít video hơn giới hạn),
    # nhưng dùng đúng số bạn đã yêu cầu làm mẫu số là hợp lý nhất có thể; nếu
    # tải vượt mốc này (mode "0 = không giới hạn"), on_poll trong douyin_dl.py
    # tự nới `job.total` lên theo thực tế (xem `job.total = max(...)`).
    if body.mode == "single":
        total_hint = 1
    elif body.mode in ("profile", "info"):
        counts = [n for n in body.number.values() if n > 0]
        total_hint = sum(counts) if counts else 1
    else:
        total_hint = max(body.search_max, 1)

    def target(job: jobs.JobState) -> None:
        # Không đặt sẵn 1 item tĩnh "Đang tải" — douyin_dl.py tự quét thư mục
        # đích mỗi 0.5s và APPEND 1 JobItem thật cho mỗi video vừa tải xong
        # (xem douyin_dl.py::_run_download/on_poll), nên job.items phản ánh
        # đúng danh sách video thật thay vì 1 dòng trạng thái chung chung.
        job.items = []
        job.total = total_hint
        job.current_label = "Đang tải..."
        meta_run = dl.load_meta(download_id)
        meta_run["status"] = "running"
        dl.save_meta(download_id, meta_run)

        scanned_infos: list[dict] | None = None
        try:
            if body.mode == "single":
                path, _title = douyin_dl_stage.download_single(body.url, output_dir, job=job, flatten_names=True)
                files = [path]
            elif body.mode == "profile":
                files = douyin_dl_stage.download_profile_batch(
                    body.url, body.modes, body.number, output_dir, job=job, flatten_names=True
                )
            elif body.mode == "info":
                files = []
                scanned_infos = douyin_dl_stage.scan_profile_info(body.url, body.modes, body.number, output_dir, job=job)
            else:
                files = douyin_dl_stage.search_and_download(
                    body.keyword, body.search_max, output_dir, job=job, flatten_names=True
                )
        except jobs.JobCancelled:
            job.status = "cancelled"
            meta_c = dl.load_meta(download_id)
            meta_c["status"] = "failed"
            meta_c["error"] = "Đã dừng theo yêu cầu người dùng"
            dl.save_meta(download_id, meta_c)
            return
        except douyin_dl_stage.DouyinDlError as err:
            job.status = "failed"
            job.error = str(err)
            meta_err = dl.load_meta(download_id)
            meta_err["status"] = "failed"
            meta_err["error"] = str(err)
            dl.save_meta(download_id, meta_err)
            return
        except Exception as err:
            logger.exception("Tải video lỗi {}", download_id)
            job.status = "failed"
            job.error = str(err)
            meta_err2 = dl.load_meta(download_id)
            meta_err2["status"] = "failed"
            meta_err2["error"] = str(err)
            dl.save_meta(download_id, meta_err2)
            return

        result_count = len(scanned_infos) if scanned_infos is not None else len(files)
        job.done_count = result_count
        job.total = max(result_count, 1)
        job.status = "done"
        meta_done = dl.load_meta(download_id)
        meta_done["status"] = "done"
        if scanned_infos is not None:
            # mode "info" — chỉ lấy thông tin, không có file video nào để show.
            meta_done["output_files"] = []
            meta_done["video_info"] = scanned_infos
        else:
            meta_done["output_files"] = [str(p.relative_to(output_dir)) for p in files]
            # Metadata đi kèm (caption/tác giả/link chia sẻ/lượt thích...) —
            # file douyin_dl.py đã đổi tên khớp đúng thứ tự với `files` khi
            # flatten (xem douyin_dl.py::_run_download on_poll, "{n}.json"
            # cạnh "{n}.mp4").
            video_info = []
            for p in files:
                info = douyin_dl_stage.read_video_info(p.with_suffix(".json"))
                video_info.append(info)
            meta_done["video_info"] = video_info
        dl.save_meta(download_id, meta_done)

    jobs.start_job(f"download:{download_id}", 1, target)
    return {"download_id": download_id, "status": "started"}


@app.get("/api/downloads/{download_id}/jobs/status", response_model=JobStatusResponse)
def download_job_status_route(download_id: str):
    job = jobs.get_job(f"download:{download_id}")
    if job is None:
        try:
            meta = dl.load_meta(download_id)
        except FileNotFoundError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        orphaned = meta.get("status") == "running"
        if orphaned:
            meta["status"] = "failed"
            meta["error"] = "Phiên trước bị gián đoạn (server restart) — bấm tải lại."
            dl.save_meta(download_id, meta)
        return JobStatusResponse(registered=False, orphaned=orphaned)
    return JobStatusResponse(
        registered=True,
        status=job.status,
        total=job.total,
        done_count=job.done_count,
        current_label=job.current_label,
        items=[JobItemResponse(id=it.id, label=it.label, status=it.status, error=it.error) for it in job.items],
        error=job.error,
        started_at=job.started_at,
    )


@app.post("/api/downloads/{download_id}/jobs/cancel")
def cancel_download_job_route(download_id: str):
    ok = jobs.request_cancel(f"download:{download_id}")
    if not ok:
        raise HTTPException(status_code=409, detail="Không có job nào đang chạy")
    return {"status": "cancelling"}


@app.get("/api/downloads/{download_id}/files/{filename:path}")
def download_file_route(download_id: str, filename: str):
    try:
        meta = dl.load_meta(download_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    if filename not in (meta.get("output_files") or []):
        raise HTTPException(status_code=404, detail="Không tìm thấy file")
    output_dir = meta.get("output_dir") or str(dl.download_dir(download_id) / "files")
    path = Path(output_dir) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File không còn trên đĩa")
    return FileResponse(path, filename=path.name)


@app.post("/api/downloads/{download_id}/reveal")
def reveal_download_route(download_id: str):
    try:
        meta = dl.load_meta(download_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    output_dir = meta.get("output_dir") or str(dl.download_dir(download_id) / "files")
    target = Path(output_dir)
    files = meta.get("output_files") or []
    select_path = target / files[0] if files else target
    if not select_path.exists():
        raise HTTPException(status_code=404, detail="Thư mục/file không còn trên đĩa")
    try:
        if files:
            subprocess.run(["explorer", f"/select,{select_path}"], check=False)
        else:
            subprocess.run(["explorer", str(select_path)], check=False)
    except FileNotFoundError as err:
        raise HTTPException(status_code=500, detail="Chỉ hỗ trợ mở Explorer trên Windows") from err
    return {"status": "ok"}


@app.delete("/api/downloads/{download_id}", status_code=204)
def delete_download_route(download_id: str):
    if jobs.is_job_running(f"download:{download_id}"):
        raise HTTPException(status_code=409, detail="Đang tải — đợi xong hoặc bấm Dừng trước")
    try:
        dl.delete_download(download_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


# ------------------------------------------------------------------ Dự án tự động (crawl Douyin → pipeline → hàng đợi đăng)


@app.get("/api/social", response_model=list[SocialProjectSummary])
def list_social_route():
    return sp.list_social_projects()


@app.post("/api/social", status_code=201)
def create_social_route(body: CreateSocialProjectRequest):
    try:
        return sp.create_social_project(body.title, body.douyin_profile_url, body.posts_per_day)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@app.get("/api/social/{social_id}")
def get_social_route(social_id: str):
    try:
        return sp.load_state(social_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@app.patch("/api/social/{social_id}")
def update_social_route(social_id: str, body: UpdateSocialProjectRequest):
    try:
        with sp.locked_state(social_id) as state:
            if body.title is not None:
                state.title = body.title.strip() or state.title
            if body.status is not None:
                state.status = body.status
            if body.posts_per_day is not None and body.posts_per_day != state.posts_per_day:
                state.posts_per_day = body.posts_per_day
                # Đổi số bài/ngày → tính lại giờ hẹn theo nhịp mới ngay, không
                # bắt chờ hết giờ hẹn cũ (có thể đã tính theo nhịp thưa hơn).
                if state.last_post_at is not None:
                    state.next_post_at = _compute_next_post_at(state.last_post_at, state.posts_per_day)
            if body.engine is not None:
                state.engine = body.engine
            if body.tts_engine is not None:
                state.tts_engine = body.tts_engine
            if body.voice is not None:
                state.voice = body.voice
            if body.audio_mode is not None:
                state.audio_mode = body.audio_mode
            if body.original_audio_volume_db is not None:
                state.original_audio_volume_db = body.original_audio_volume_db
            if body.music_volume_db is not None:
                state.music_volume_db = body.music_volume_db
            if body.subtitle_font_size is not None:
                state.subtitle_font_size = body.subtitle_font_size
            if body.min_video_speed is not None:
                state.min_video_speed = body.min_video_speed
            if body.use_viesnap_fallback is not None:
                state.use_viesnap_fallback = body.use_viesnap_fallback
            if body.crawl_via_browser is not None:
                state.crawl_via_browser = body.crawl_via_browser
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    sp.sync_index_entry(social_id)
    return sp.load_state(social_id)


@app.post("/api/social/{social_id}/ocr-crop-region")
def update_social_ocr_crop_region(social_id: str, body: UpdateOcrCropRegionRequest):
    try:
        with sp.locked_state(social_id) as state:
            state.ocr_crop_region = body.region
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return {"status": "ok"}


@app.delete("/api/social/{social_id}", status_code=204)
def delete_social_route(social_id: str):
    if jobs.is_job_running(f"social:{social_id}:crawl"):
        raise HTTPException(status_code=409, detail="Đang crawl — đợi xong hoặc bấm Dừng trước")
    try:
        sp.delete_social_project(social_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


def _start_social_crawl(social_id: str, limit: int | None = None) -> jobs.JobState | None:
    """Tách khỏi route để bộ lập lịch nền (`_social_scheduler_loop`) gọi lại
    được y hệt logic bấm tay "Crawl ngay" — `jobs.start_job` tự chặn trùng
    (trả None nếu đã có job cùng key đang chạy) nên gọi lại an toàn dù route
    và scheduler cùng lúc kích hoạt. `limit` = số video MỚI NHẤT quét (None/0
    = quét hết toàn bộ trang, không giới hạn) — trang cá nhân Douyin trả
    video theo thứ tự MỚI NHẤT trước, nên limit luôn lấy đúng N video mới
    nhất, không phải N video ngẫu nhiên."""

    def target(job: jobs.JobState) -> None:
        job.current_label = "Đang chờ tới lượt gọi API Douyin..."
        _wait_for_douyin_api_slot()
        job.current_label = "Đang quét video mới..."
        state = sp.load_state(social_id)
        dest_dir = sp.social_dir(social_id) / "_crawl_tmp"

        def on_browser_progress(n: int) -> None:
            job.done_count = n
            job.total = max(job.total, n)
            job.current_label = f"Chrome: đã lấy thông tin {n} video..."

        try:
            if state.crawl_via_browser:
                job.current_label = "Đang mở Chrome và cuộn trang kênh Douyin..."
                infos = douyin_browser_stage.crawl_profile(
                    state.douyin_profile_url,
                    limit=limit or None,
                    on_progress=on_browser_progress,
                    should_stop=job.cancel_event.is_set,
                    screenshot_dir=sp.social_dir(social_id) / "screenshots",
                )
                if job.cancel_event.is_set():
                    raise jobs.JobCancelled("Đã dừng theo yêu cầu người dùng")
            else:
                infos = douyin_dl_stage.scan_profile_info(
                    state.douyin_profile_url, ["post"], {"post": limit or 0}, dest_dir, job=job
                )
        except jobs.JobCancelled:
            job.status = "cancelled"
            return
        except Exception as err:
            logger.exception("Crawl dự án tự động lỗi {}", social_id)
            job.status = "failed"
            job.error = str(err)
            return
        finally:
            shutil.rmtree(dest_dir, ignore_errors=True)

        with sp.locked_state(social_id) as s:
            by_id = {item.aweme_id: item for item in s.queue}
            added = 0
            backfilled = 0
            for info in infos:
                aweme_id = info.get("aweme_id") or ""
                if not aweme_id:
                    continue
                existing = by_id.get(aweme_id)
                if existing is None:
                    s.queue.append(
                        QueueItem(
                            aweme_id=aweme_id,
                            title=info.get("title") or "",
                            play_url=info.get("play_url") or "",
                            share_url=info.get("share_url") or "",
                            thumb_url=info.get("thumb_url"),
                            duration_sec=info.get("duration_sec") or 0,
                            discovered_at=datetime.now(),
                        )
                    )
                    by_id[aweme_id] = s.queue[-1]
                    added += 1
                elif not existing.share_url and info.get("share_url"):
                    # Video crawl từ TRƯỚC khi có field `share_url` (thêm hôm
                    # nay) — vá lại ngay khi crawl lần sau tình cờ quét lại
                    # đúng video đó, để lần kích hoạt tới không còn phải rơi
                    # về `play_url` cũ (thường đã hết hạn, gây 403) nữa.
                    existing.share_url = info.get("share_url") or ""
                    backfilled += 1
                if existing is not None and existing.status == QueueItemStatus.pending and info.get("play_url"):
                    # Video chưa xử lý có mặt lại trong lần crawl này → thay
                    # bằng play_url MỚI (link cũ có chữ ký hết hạn nhanh), để
                    # lúc kích hoạt tải được ngay, khỏi phải dò lại link.
                    existing.play_url = info.get("play_url") or existing.play_url
            # "Làm mới" hàng đợi: bỏ các video CHỜ XỬ LÝ không có mặt trong lần
            # crawl này (vd còn sót từ 1 lần "Crawl tất cả" trước đó). Chỉ đụng
            # tới `pending` — video đã đăng/đang xử lý/sẵn sàng/lỗi/bỏ qua
            # được giữ làm lịch sử: xoá video đã đăng thì lần crawl sau có thể
            # đưa nó quay lại thành "chờ xử lý" và đăng trùng. Crawl rỗng (lỗi
            # tạm) thì không xoá gì.
            removed = 0
            if infos:
                crawled_ids = {info.get("aweme_id") for info in infos if info.get("aweme_id")}
                before = len(s.queue)
                s.queue = [
                    i for i in s.queue if i.status != QueueItemStatus.pending or i.aweme_id in crawled_ids
                ]
                removed = before - len(s.queue)
            s.last_crawl_at = datetime.now()
            if infos:
                # API Douyin trả dữ liệu bình thường → không còn bị khoá.
                s.douyin_backoff_level = 0
        # Log tạm để chẩn đoán — có bao nhiêu info Douyin trả về THẬT SỰ có
        # share_url (so với added/backfilled) — giữ lại vài lần crawl tới để
        # xác nhận field này có bị Douyin bỏ trống ở chế độ quét "post" hay
        # không, tránh đoán mò lần nữa.
        with_share = sum(1 for info in infos if info.get("share_url"))
        logger.info(
            "crawl_social: {} info nhận được, {} có share_url, {} video mới, {} video cũ vá được share_url, "
            "{} video chờ xử lý cũ bị bỏ (không có trong lần crawl này)",
            len(infos), with_share, added, backfilled, removed,
        )
        sp.sync_index_entry(social_id)
        job.done_count = added
        job.total = max(added, 1)
        job.current_label = (
            f"Xong — quét {len(infos)} video, thêm {added} video mới"
            + (f", bỏ {removed} video chờ cũ không còn trong danh sách" if removed else "")
            + (f", vá share_url {backfilled} video cũ" if backfilled else "")
        )
        job.status = "done"

    return jobs.start_job(f"social:{social_id}:crawl", 1, target)


@app.post("/api/social/{social_id}/crawl", status_code=202)
def crawl_social_route(social_id: str, body: CrawlSocialRequest | None = None):
    try:
        sp.load_state(social_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    job = _start_social_crawl(social_id, limit=(body.limit if body else None))
    if job is None:
        raise HTTPException(status_code=409, detail="Đang crawl cho dự án này")
    return {"status": "started"}


@app.get("/api/social/{social_id}/jobs/crawl", response_model=JobStatusResponse)
def social_crawl_job_status(social_id: str):
    job = jobs.get_job(f"social:{social_id}:crawl")
    if job is None:
        return JobStatusResponse(registered=False)
    return JobStatusResponse(
        registered=True,
        status=job.status,
        total=job.total,
        done_count=job.done_count,
        current_label=job.current_label,
        items=[JobItemResponse(id=it.id, label=it.label, status=it.status, error=it.error) for it in job.items],
        error=job.error,
        started_at=job.started_at,
    )


@app.post("/api/social/{social_id}/jobs/crawl/cancel")
def cancel_social_crawl_route(social_id: str):
    ok = jobs.request_cancel(f"social:{social_id}:crawl")
    return {"status": "ok" if ok else "not_running"}


def _start_social_activate(social_id: str, aweme_id: str) -> dict:
    """Tách khỏi route để bộ lập lịch nền gọi lại y hệt logic bấm tay "Kích
    hoạt" — raise ValueError với thông điệp tiếng Việt sẵn cho route dùng lại
    làm HTTPException detail, scheduler chỉ log rồi bỏ qua tick này."""
    try:
        social_state = sp.load_state(social_id)
    except FileNotFoundError as err:
        raise ValueError(str(err)) from err
    item = next((i for i in social_state.queue if i.aweme_id == aweme_id), None)
    if item is None:
        raise ValueError("Không tìm thấy video này trong hàng đợi")
    # Cho phép kích hoạt LẠI 1 video lỗi ngay ở bước tải (chưa hề có video
    # xuất ra) — khác video lỗi ở bước đăng (đã có pipeline/video xong, không
    # nên chạy lại từ đầu qua đường này).
    retryable_activate_failure = item.status == QueueItemStatus.failed and item.failed_stage == "activate"
    if item.status != QueueItemStatus.pending and not retryable_activate_failure:
        raise ValueError(f"Video đang ở trạng thái '{item.status.value}', không kích hoạt lại được")
    if not item.play_url:
        raise ValueError("Video này không có link tải trực tiếp")

    # KHÔNG dịch tiêu đề ở bước này — người dùng chốt: chỉ dịch NGAY TRƯỚC LÚC
    # đăng bài thật (xem `_start_social_publish`), không phải lúc kích hoạt.
    # Lý do: kích hoạt có thể thất bại (video lỗi/link hỏng) trước khi bao giờ
    # đăng được, dịch sớm ở đây vừa tốn quota Gemini vô ích vừa gây hiểu nhầm
    # (tên hiển thị trong hàng đợi đổi sang tiếng Việt dù video chưa hề đăng).
    # Tên dự án tạo ra vẫn dùng `aweme_id` (số, ASCII) làm phần phân biệt
    # chính — `item.title` gốc thường toàn tiếng Trung, sau khi bỏ dấu
    # (slugify) gần như bị xoá sạch, nên KHÔNG dùng làm phần chống trùng slug
    # (đã xác nhận thật qua lỗi 500 "đã tồn tại" trước đây) — chỉ dùng để
    # người xem dễ nhận diện dự án nào là video nào.
    title = f"{social_state.title} {aweme_id} - {(item.title or '')[:60]}".strip()[:120]
    try:
        project = pj.create_project(title, project_type="single")
    except ValueError:
        # Phòng thêm 1 lớp nữa (không chỉ dựa vào aweme_id trong title) —
        # nếu vẫn trùng vì lý do khác (vd kích hoạt lại đúng video đã có dự
        # án dở dang bị xoá tay), tự thêm hậu tố tăng dần thay vì lỗi 500.
        n = 2
        while True:
            try:
                project = pj.create_project(f"{title} ({n})", project_type="single")
                break
            except ValueError:
                n += 1
                if n > 50:
                    raise
    root = pj.project_dir(project.project_id)

    with pj.locked_project(project.project_id) as s:
        s.social_link = SocialLink(social_id=social_id, aweme_id=aweme_id)
        s.auto_pipeline = True
        s.auto_engine = social_state.engine
        s.auto_ocr_crop_region = social_state.ocr_crop_region
        s.auto_tts_engine = social_state.tts_engine
        s.auto_voice = social_state.voice
        s.auto_audio_mode = social_state.audio_mode
        s.auto_original_audio_volume_db = social_state.original_audio_volume_db
        s.auto_music_volume_db = social_state.music_volume_db
        s.auto_subtitle_font_size = social_state.subtitle_font_size
        s.auto_min_video_speed = social_state.min_video_speed

    with sp.locked_state(social_id) as ss:
        for i in ss.queue:
            if i.aweme_id == aweme_id:
                i.status = QueueItemStatus.processing
                i.project_id = project.project_id
                i.error = None
                i.failed_stage = None
                break

    def target(job: jobs.JobState) -> None:
        job.items = [jobs.JobItem(id="fetch", label="Tải video")]
        job.items[0].status = "running"
        with pj.locked_project(project.project_id) as s:
            s.stages["ingest"].status = StageStatus.running

        def on_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        def fail_ingest(err: BaseException) -> None:
            logger.exception("Kích hoạt video dự án tự động lỗi {}", project.project_id)
            job.items[0].status = "failed"
            job.items[0].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project.project_id) as s:
                s.stages["ingest"].status = StageStatus.failed
                s.stages["ingest"].error = str(err)
            try:
                with sp.locked_state(social_id) as ss:
                    for i in ss.queue:
                        if i.aweme_id == aweme_id:
                            i.status = QueueItemStatus.failed
                            i.error = str(err)
                            i.failed_stage = "activate"
                            break
            except FileNotFoundError:
                pass

        # `item.play_url` là link CDN Douyin CÓ CHỮ KÝ HẾT HẠN NHANH — nhưng
        # THƯỜNG VẪN DÙNG ĐƯỢC nếu kích hoạt sớm sau lúc crawl. Thử tải bằng
        # bản đã lưu TRƯỚC — chỉ khi thất bại mới gọi API Douyin dò lại link
        # mới (đã xác nhận thật: gọi API dò lại ở MỌI lần kích hoạt, kể cả
        # khi link cũ vẫn còn dùng được, là nguyên nhân chính khiến tài khoản
        # bị risk-control tạm khoá API — xem `DOUYIN_API_MIN_INTERVAL_S`).
        try:
            dest, original, duration = fetch_url_stage.download_from_direct_url(
                root, item.play_url, item.title or aweme_id, on_progress=on_progress
            )
        except jobs.JobCancelled:
            _mark_cancelled(project.project_id, "ingest", job)
            return
        except Exception as first_err:
            # Tầng dự phòng 2: dịch vụ bên thứ 3 (viesnap) — dò lại play_url
            # KHÔNG cần gọi API Douyin của mình (không tốn hạn mức, không
            # góp phần risk-control) — đã xác nhận thật hoạt động, kể cả với
            # link trần dựng từ aweme_id (không cần `share_url` có sẵn hay
            # không). Chỉ khi tầng này CŨNG thất bại mới rơi xuống tầng cuối
            # (gọi thẳng API Douyin, có giới hạn tốc độ + risk-control).
            viesnap_result = None
            if social_state.use_viesnap_fallback:
                video_url = item.share_url or f"https://www.douyin.com/video/{aweme_id}"
                viesnap_result = fetch_url_stage.download_from_viesnap(
                    root, video_url, item.title or aweme_id, on_progress=on_progress
                )
            if viesnap_result is not None:
                dest, original, duration = viesnap_result
            else:
                fresh_play_url = None
                if social_state.crawl_via_browser:
                    # Dò lại bằng Chrome thật: mở trang video như người xem,
                    # chỉ cần aweme_id (không cần share_url).
                    _wait_for_douyin_api_slot()
                    fresh = douyin_browser_stage.fetch_video_info(
                        aweme_id, screenshot_dir=sp.social_dir(social_id) / "screenshots"
                    )
                    fresh_play_url = (fresh or {}).get("play_url") or None
                elif item.share_url:
                    _wait_for_douyin_api_slot()
                    refetch_dir = sp.social_dir(social_id) / "_activate_refetch"
                    try:
                        fresh_infos = douyin_dl_stage.scan_profile_info(item.share_url, ["post"], {"post": 0}, refetch_dir)
                        fresh_play_url = next((i.get("play_url") for i in fresh_infos if i.get("play_url")), None)
                    except Exception as err:
                        logger.warning("Kích hoạt '{}': dò lại play_url mới thất bại ({})", aweme_id, err)
                    finally:
                        shutil.rmtree(refetch_dir, ignore_errors=True)
                if social_state.crawl_via_browser or item.share_url:
                    if not fresh_play_url:
                        # Dò lại 1 video ĐÃ BIẾT `share_url` (chắc chắn tồn
                        # tại) mà không ra kết quả gì — dấu hiệu đáng tin cậy
                        # của risk-control (khác "crawl 0 kết quả mới", có
                        # thể do kênh thật sự không có gì mới) — tự đặt giờ
                        # nghỉ, bộ lập lịch bỏ qua crawl/kích hoạt dự án này
                        # tới lúc đó.
                        try:
                            with sp.locked_state(social_id) as s_backoff:
                                s_backoff.douyin_backoff_level += 1
                                backoff_h = _douyin_backoff_hours(s_backoff.douyin_backoff_level)
                                s_backoff.douyin_backoff_until = datetime.now() + timedelta(hours=backoff_h)
                                backoff_level = s_backoff.douyin_backoff_level
                            logger.warning(
                                "Kích hoạt '{}': nghi Douyin risk-control (dò link cho video đã biết share_url "
                                "vẫn ra rỗng, lần liên tiếp thứ {}) — dự án '{}' tạm nghỉ gọi API Douyin {:.0f}h",
                                aweme_id, backoff_level, social_id, backoff_h,
                            )
                        except FileNotFoundError:
                            pass
                    else:
                        _reset_douyin_backoff_level(social_id)
                if not fresh_play_url or fresh_play_url == item.play_url:
                    fail_ingest(first_err)
                    return
                try:
                    dest, original, duration = fetch_url_stage.download_from_direct_url(
                        root, fresh_play_url, item.title or aweme_id, on_progress=on_progress
                    )
                except jobs.JobCancelled:
                    _mark_cancelled(project.project_id, "ingest", job)
                    return
                except Exception as second_err:
                    fail_ingest(second_err)
                    return

        job.items[0].status = "done"
        job.done_count = job.total
        job.status = "done"
        _finalize_ingest(project.project_id, root, dest, original, duration, f"tự động từ Douyin: {original}")

    jobs.start_job(f"{project.project_id}:ingest", 1, target)
    return {"status": "started", "project_id": project.project_id}


@app.post("/api/social/{social_id}/queue/{aweme_id}/activate", status_code=202)
def activate_queue_item_route(social_id: str, aweme_id: str):
    """Bấm tay "Kích hoạt"/"Kích hoạt lại" — tôn trọng hàng đợi chung (toàn hệ
    thống chỉ xử lý 1 video tại 1 thời điểm):
    - video LỖI → đưa về "chờ xử lý"; hàng đợi chung tự xử lý lại đúng thứ tự
      (nó là video cũ nhất của dự án nên được làm trước), và dự án hết bị dừng
      nếu không còn video lỗi nào khác.
    - video CHỜ → chạy ngay nếu hiện không có video nào đang xử lý, còn không
      thì báo để người dùng biết nó sẽ được xử lý theo lượt."""
    try:
        state = sp.load_state(social_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    item = next((i for i in state.queue if i.aweme_id == aweme_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy video này trong hàng đợi")
    if item.status == QueueItemStatus.failed:
        with sp.locked_state(social_id) as s:
            for i in s.queue:
                if i.aweme_id == aweme_id and i.status == QueueItemStatus.failed:
                    i.status = QueueItemStatus.pending
                    i.error = None
                    i.failed_stage = None
        return {"status": "queued"}
    if _processing_items(_load_all_social_states()):
        raise HTTPException(
            status_code=409,
            detail="Đang xử lý một video khác — video này sẽ được xử lý tự động khi tới lượt (xem Giám sát tiến trình)",
        )
    try:
        return _start_social_activate(social_id, aweme_id)
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err


@app.post("/api/social/{social_id}/queue/{aweme_id}/skip")
def skip_queue_item_route(social_id: str, aweme_id: str):
    """Đánh dấu 1 video là BỎ QUA — không tự kích hoạt/đăng nữa (bộ lập lịch
    nền chỉ chọn video `pending`/`ready`, video `skipped` bị loại khỏi cả 2
    vòng chọn). Chỉ chặn khi đã `posted` — mọi trạng thái khác (kể cả đang
    `processing`/`failed`) đều bỏ qua được, người dùng tự quyết không muốn
    đăng video đó nữa vì bất kỳ lý do gì."""
    try:
        with sp.locked_state(social_id) as state:
            item = next((i for i in state.queue if i.aweme_id == aweme_id), None)
            if item is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy video này trong hàng đợi")
            if item.status == QueueItemStatus.posted:
                raise HTTPException(status_code=409, detail="Video đã đăng rồi, không bỏ qua được")
            item.status = QueueItemStatus.skipped
            item.error = None
            item.failed_stage = None
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return {"status": "ok"}


@app.post("/api/social/{social_id}/queue/{aweme_id}/unskip")
def unskip_queue_item_route(social_id: str, aweme_id: str):
    """Huỷ đánh dấu bỏ qua — trả video về `pending`, bộ lập lịch/nút "Kích
    hoạt" lại thấy video này bình thường."""
    try:
        with sp.locked_state(social_id) as state:
            item = next((i for i in state.queue if i.aweme_id == aweme_id), None)
            if item is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy video này trong hàng đợi")
            if item.status != QueueItemStatus.skipped:
                raise HTTPException(
                    status_code=409, detail=f"Video đang ở trạng thái '{item.status.value}', không phải đã bỏ qua"
                )
            item.status = QueueItemStatus.pending
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return {"status": "ok"}


# ------------------------------------------------------------------ Chrome thật cho crawl Douyin
# 1 profile Chrome DÙNG CHUNG cho mọi dự án tự động (không theo dự án) —
# Douyin tính risk-control theo tài khoản/thiết bị, nhiều profile trên cùng 1
# máy/IP không giúp gì mà còn tốn công đăng nhập từng cái.

DOUYIN_BROWSER_LOGIN_JOB = "douyin_browser:login"


@app.get("/api/douyin-browser/status")
def douyin_browser_status_route():
    return {
        **douyin_browser_stage.login_status(),
        "profile_ready": douyin_browser_stage.PROFILE_DIR.exists(),
    }


@app.post("/api/douyin-browser/login", status_code=202)
def douyin_browser_login_route():
    def target(job: jobs.JobState) -> None:
        job.current_label = "Đang chờ bạn đăng nhập Douyin trong cửa sổ Chrome (xong thì đóng cửa sổ)..."
        try:
            ok = douyin_browser_stage.login_interactive(should_stop=job.cancel_event.is_set)
        except Exception as err:
            logger.exception("Đăng nhập Douyin (Chrome) lỗi")
            job.status = "failed"
            job.error = str(err)
            return
        job.status = "done"
        job.current_label = "Đã đăng nhập Douyin" if ok else "Đã đóng cửa sổ — CHƯA thấy cookie đăng nhập Douyin"

    job = jobs.start_job(DOUYIN_BROWSER_LOGIN_JOB, 1, target)
    if job is None:
        raise HTTPException(status_code=409, detail="Cửa sổ đăng nhập Douyin đang mở")
    return {"status": "started"}


@app.get("/api/douyin-browser/login-job", response_model=JobStatusResponse)
def douyin_browser_login_job_route():
    job = jobs.get_job(DOUYIN_BROWSER_LOGIN_JOB)
    if job is None:
        return JobStatusResponse(registered=False)
    return JobStatusResponse(
        registered=True,
        status=job.status,
        total=job.total,
        done_count=job.done_count,
        current_label=job.current_label,
        items=[],
        error=job.error,
        started_at=job.started_at,
    )


def _tiktok_profile_dir(social_id: str, state: object) -> Path:
    path = getattr(state, "tiktok_session_path", "") or ""
    return Path(path) if path else sp.social_dir(social_id) / "tiktok_profile"


@app.get("/api/social/{social_id}/tiktok/status")
def tiktok_login_status_route(social_id: str):
    try:
        state = sp.load_state(social_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    profile_dir = _tiktok_profile_dir(social_id, state)
    return {"logged_in": social_publish_stage.has_logged_in_session(profile_dir)}


@app.post("/api/social/{social_id}/tiktok/login", status_code=202)
def tiktok_login_route(social_id: str):
    try:
        state = sp.load_state(social_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    profile_dir = _tiktok_profile_dir(social_id, state)
    if not state.tiktok_session_path:
        with sp.locked_state(social_id) as s:
            s.tiktok_session_path = str(profile_dir)

    def target(job: jobs.JobState) -> None:
        job.current_label = "Đang chờ bạn đăng nhập TikTok trong cửa sổ Chrome..."
        try:
            social_publish_stage.login_tiktok_interactive(
                profile_dir, should_stop=lambda: job.cancel_event.is_set()
            )
        except Exception as err:
            logger.exception("Đăng nhập TikTok lỗi {}", social_id)
            job.status = "failed"
            job.error = str(err)
            return
        job.status = "done"
        job.current_label = "Đã đóng cửa sổ đăng nhập"

    job = jobs.start_job(f"social:{social_id}:tiktok_login", 1, target)
    if job is None:
        raise HTTPException(status_code=409, detail="Đang mở cửa sổ đăng nhập cho dự án này")
    return {"status": "started"}


@app.get("/api/social/{social_id}/jobs/tiktok_login", response_model=JobStatusResponse)
def tiktok_login_job_status(social_id: str):
    job = jobs.get_job(f"social:{social_id}:tiktok_login")
    if job is None:
        return JobStatusResponse(registered=False)
    return JobStatusResponse(
        registered=True,
        status=job.status,
        total=job.total,
        done_count=job.done_count,
        current_label=job.current_label,
        items=[],
        error=job.error,
        started_at=job.started_at,
    )


@app.post("/api/social/{social_id}/jobs/tiktok_login/cancel")
def cancel_tiktok_login_route(social_id: str):
    ok = jobs.request_cancel(f"social:{social_id}:tiktok_login")
    return {"status": "ok" if ok else "not_running"}


def _start_social_publish(social_id: str, aweme_id: str) -> jobs.JobState | None:
    """Tách khỏi route để bộ lập lịch nền gọi lại y hệt logic bấm tay "Đăng
    lên TikTok" — raise ValueError (thông điệp tiếng Việt) cho lỗi tiền kiểm
    tra, route dùng lại làm HTTPException detail."""
    try:
        state = sp.load_state(social_id)
    except FileNotFoundError as err:
        raise ValueError(str(err)) from err
    item = next((i for i in state.queue if i.aweme_id == aweme_id), None)
    if item is None:
        raise ValueError("Không tìm thấy video này trong hàng đợi")
    if item.status not in (QueueItemStatus.ready, QueueItemStatus.failed):
        raise ValueError(f"Video đang ở trạng thái '{item.status.value}', chưa sẵn sàng đăng")
    if not item.project_id:
        raise ValueError("Video này chưa gắn với project pipeline nào")
    video_path = pj.project_dir(item.project_id) / "export" / "final.mp4"
    if not video_path.exists():
        raise ValueError("Không tìm thấy video đã xuất — kiểm tra lại project pipeline")
    profile_dir = _tiktok_profile_dir(social_id, state)

    # Dịch tiêu đề (thường tiếng Trung) sang tiếng Việt NGAY TRƯỚC LÚC đăng —
    # người dùng chốt: chỉ dịch khi thật sự chuẩn bị đăng bài, không phải lúc
    # kích hoạt (xem comment ở `_start_social_activate`). Dịch lười theo
    # nghĩa hẹp hơn nữa — nếu đã dịch từ lần đăng thử trước (item.title_vi có
    # sẵn), dùng lại luôn, không gọi lại Gemini.
    title_vi = item.title_vi or translate_stage.translate_title(item.title)
    if title_vi != item.title_vi:
        with sp.locked_state(social_id) as s_title:
            for i in s_title.queue:
                if i.aweme_id == aweme_id:
                    i.title_vi = title_vi
                    break
    caption = title_vi or item.title or ""

    # Xoá lỗi cũ (nếu có, vd từ lần đăng thất bại trước) ngay khi bắt đầu thử
    # lại — nếu không, lỗi cũ tồn tại vĩnh viễn trên UI kể cả sau khi đăng
    # thành công (đã xác nhận thật: field `error` trước đây CHỈ được set khi
    # thất bại, không bao giờ được xoá ở nhánh thành công).
    with sp.locked_state(social_id) as s0:
        for i in s0.queue:
            if i.aweme_id == aweme_id:
                i.error = None
                i.failed_stage = None
                break

    def target(job: jobs.JobState) -> None:
        job.current_label = "Đang đăng video lên TikTok..."
        try:
            social_publish_stage.publish_tiktok(
                video_path, caption, profile_dir, screenshot_dir=sp.social_dir(social_id) / "logs"
            )
        except social_publish_stage.SocialPublishError as err:
            job.status = "failed"
            job.error = str(err)
            with sp.locked_state(social_id) as s:
                for i in s.queue:
                    if i.aweme_id == aweme_id:
                        i.status = QueueItemStatus.failed
                        i.error = str(err)
                        i.failed_stage = "publish"
                        break
            return
        except Exception as err:
            logger.exception("Đăng TikTok lỗi {}", social_id)
            job.status = "failed"
            job.error = str(err)
            with sp.locked_state(social_id) as s:
                for i in s.queue:
                    if i.aweme_id == aweme_id:
                        i.status = QueueItemStatus.failed
                        i.error = str(err)
                        i.failed_stage = "publish"
                        break
            return

        job.status = "done"
        with sp.locked_state(social_id) as s:
            s.last_post_at = datetime.now()
            s.next_post_at = _compute_next_post_at(s.last_post_at, s.posts_per_day)
            for i in s.queue:
                if i.aweme_id == aweme_id:
                    i.status = QueueItemStatus.posted
                    i.posted_at = datetime.now()
                    break

    return jobs.start_job(f"social:{social_id}:{aweme_id}:publish", 1, target)


@app.post("/api/social/{social_id}/queue/{aweme_id}/publish", status_code=202)
def publish_queue_item_route(social_id: str, aweme_id: str):
    try:
        job = _start_social_publish(social_id, aweme_id)
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    if job is None:
        raise HTTPException(status_code=409, detail="Đang đăng video này")
    return {"status": "started"}


@app.get("/api/social/{social_id}/queue/{aweme_id}/jobs/publish", response_model=JobStatusResponse)
def publish_queue_item_job_status(social_id: str, aweme_id: str):
    job = jobs.get_job(f"social:{social_id}:{aweme_id}:publish")
    if job is None:
        return JobStatusResponse(registered=False)
    return JobStatusResponse(
        registered=True,
        status=job.status,
        total=job.total,
        done_count=job.done_count,
        current_label=job.current_label,
        items=[],
        error=job.error,
        started_at=job.started_at,
    )


# ------------------------------------------------------------------ Bộ lập lịch nền cho "Dự án tự động" (Phase 2)
# 1 vòng lặp chạy trong thread daemon riêng, khởi động cùng FastAPI — KHÔNG
# đụng tới bất kỳ project/social project nào không bật `status="active"`,
# và luôn tái dùng đúng 3 hàm `_start_social_*` mà route bấm tay cũng gọi
# (không có nhánh logic riêng cho "tự động" vs "bấm tay" — tránh 2 luồng
# code làm 2 việc hơi khác nhau rồi lệch pha theo thời gian).

SOCIAL_SCHEDULER_TICK_S = 60.0
SOCIAL_CRAWL_INTERVAL_H = 24.0
# Số video chuẩn bị sẵn của mỗi dự án giờ theo posts_per_day (đủ cho hôm nay
# + tối đa 1 ngày chuẩn bị trước) — xem `_pipeline_plan`. Không kích hoạt dư
# thừa nhanh hơn tốc độ đăng thật: mỗi lần kích hoạt có thể là 1 lần gọi API
# Douyin dò link, và tài khoản Douyin từng bị risk-control tạm khoá API sau
# ~20-30 lần gọi dồn dập trong vài giờ.

# Giãn cách TỐI THIỂU giữa 2 lần gọi API Douyin bất kỳ (crawl HOẶC dò lại
# play_url lúc kích hoạt) — DÙNG CHUNG cho MỌI dự án tự động, không phải
# riêng từng dự án, vì Douyin risk-control tính theo COOKIE/THIẾT BỊ gọi,
# không phải theo dự án. Trước đây không có giãn cách nào — nhiều dự án +
# nhiều lần kích hoạt liên tiếp gọi API gần như liền nhau, đúng kiểu hành vi
# bot bị risk-control phát hiện.
DOUYIN_API_MIN_INTERVAL_S = 90.0
# Cộng thêm 0..N giây ngẫu nhiên vào giãn cách tối thiểu — nhịp gọi đều tăm
# tắp đúng 90s là dấu hiệu máy móc dễ nhận ra hơn nhịp có dao động.
DOUYIN_API_JITTER_S = 60.0
_douyin_api_gate_lock = threading.Lock()
_last_douyin_api_call_at = 0.0

# Thời gian nghỉ API Douyin khi nghi risk-control, tăng dần theo số lần bị
# LIÊN TIẾP (`SocialProjectState.douyin_backoff_level`): lần 1 nghỉ 3h, lần 2
# 12h, từ lần 3 trở đi 48h — bị khoá lại ngay sau khi hết nghỉ nghĩa là 3h
# chưa đủ, cứ thử lại nhịp cũ chỉ làm tài khoản bị khoá lâu hơn.
DOUYIN_BACKOFF_HOURS = (3.0, 12.0, 48.0)


def _douyin_backoff_hours(level: int) -> float:
    return DOUYIN_BACKOFF_HOURS[min(max(level, 1), len(DOUYIN_BACKOFF_HOURS)) - 1]


def _reset_douyin_backoff_level(social_id: str) -> None:
    """Gọi sau 1 lần gọi API Douyin thành công — hết chuỗi bị khoá liên tiếp."""
    try:
        with sp.locked_state(social_id) as s:
            s.douyin_backoff_level = 0
    except FileNotFoundError:
        pass


def _wait_for_douyin_api_slot() -> None:
    """Chặn (sleep) tới khi đủ giãn cách kể từ lần gọi API Douyin gần nhất —
    gọi ngay TRƯỚC mỗi lần `douyin_dl_stage.scan_profile_info(...)`. An toàn
    gọi từ nhiều thread cùng lúc (khoá `_douyin_api_gate_lock` bọc cả việc
    tính + sleep + cập nhật mốc, không phải check-rồi-sleep rời rạc dễ race)."""
    global _last_douyin_api_call_at
    with _douyin_api_gate_lock:
        min_gap = DOUYIN_API_MIN_INTERVAL_S + random.uniform(0, DOUYIN_API_JITTER_S)
        wait_s = min_gap - (time.time() - _last_douyin_api_call_at)
        if wait_s > 0:
            time.sleep(wait_s)
        _last_douyin_api_call_at = time.time()


# Chu kỳ crawl dao động ±N giờ quanh SOCIAL_CRAWL_INTERVAL_H — độ lệch suy ra
# cố định từ chính mốc `last_crawl_at` (không random lại mỗi tick, vì random
# mỗi tick 60s thì gần như luôn kích hoạt ngay khi chạm cận dưới).
SOCIAL_CRAWL_JITTER_H = 2.0


def _crawl_due(last_crawl_at: datetime | None, now: datetime) -> bool:
    if last_crawl_at is None:
        return True
    offset_h = random.Random(last_crawl_at.timestamp()).uniform(-SOCIAL_CRAWL_JITTER_H, SOCIAL_CRAWL_JITTER_H)
    return now - last_crawl_at >= timedelta(hours=SOCIAL_CRAWL_INTERVAL_H + offset_h)


# Khung giờ đăng bài (giờ máy chạy app) — giờ cao điểm lượt xem: sáng 7-9h,
# trưa 12-13h, tối 19-22h. Không đăng ngoài các khung này (đăng lúc 3h sáng
# vừa ít người xem vừa là dấu hiệu tài khoản chạy tự động).
SOCIAL_POSTING_WINDOWS: tuple[tuple[int, int], ...] = ((7, 9), (12, 13), (19, 22))
# Giãn cách giữa 2 bài dao động ±20% quanh mức trung bình.
SOCIAL_POST_GAP_JITTER = 0.2


def _in_posting_window(dt: datetime) -> bool:
    return any(start <= dt.hour < end for start, end in SOCIAL_POSTING_WINDOWS)


def _compute_next_post_at(after: datetime, posts_per_day: int) -> datetime:
    """Giờ đăng kế tiếp tính từ mốc `after` (lần đăng gần nhất).

    Giãn cách đo bằng "phút nằm trong khung giờ đăng", không phải phút đồng
    hồ: tổng các khung là 6h/ngày, nên với N bài/ngày thì mỗi bài cách nhau
    trung bình 6h/N phút-trong-khung (±20% ngẫu nhiên). Cách này tự trải đều
    đúng N bài/ngày vào các khung cao điểm, và phần thời gian ngoài khung (đêm,
    chiều) tự bị bỏ qua mà không cần xử lý riêng."""
    ppd = min(max(posts_per_day, 1), 3)
    window_min_per_day = sum((end - start) * 60 for start, end in SOCIAL_POSTING_WINDOWS)
    target = window_min_per_day / ppd * random.uniform(1 - SOCIAL_POST_GAP_JITTER, 1 + SOCIAL_POST_GAP_JITTER)
    t = after.replace(second=0, microsecond=0)
    counted = 0
    # Chặn trên 7 ngày để không bao giờ lặp vô hạn nếu cấu hình khung giờ rỗng.
    for _ in range(7 * 24 * 60):
        t += timedelta(minutes=1)
        if _in_posting_window(t):
            counted += 1
            if counted >= target:
                break
    return t + timedelta(seconds=random.randint(0, 59))


# ---- Hàng đợi CHUNG cho mọi dự án tự động
# Trước đây mỗi dự án tự kích hoạt video riêng khi hàng đợi CỦA NÓ rảnh →
# N dự án = tới N pipeline (OCR/dịch/TTS/xuất video) chạy song song trên 1
# máy, đủ làm treo máy chỉ với vài chục dự án. Giờ toàn hệ thống:
# - chỉ 1 video được XỬ LÝ tại 1 thời điểm (dự án nào tới lượt: xem
#   `_pipeline_plan`),
# - chỉ 1 bài được ĐĂNG tại 1 thời điểm (`_publish_plan`),
# - chỉ 1 lượt CRAWL tại 1 thời điểm.
# Không xong hết trong ngày thì phần tồn tự dồn sang hôm sau: giờ hẹn đăng
# kế tiếp luôn tính từ lần đăng THẬT gần nhất, nên đăng trễ không bị "đăng
# bù" dồn dập, và chỉ đăng trong khung giờ đăng.

# Video `processing` mà pipeline của nó không còn job nào chạy quá ngần này
# giây → coi là bị gián đoạn (vd server khởi động lại giữa chừng) và đánh
# dấu lỗi, nếu không nó sẽ chặn cả hàng đợi chung mãi mãi.
SOCIAL_STUCK_GRACE_S = 180.0
_stuck_since: dict[str, float] = {}

_STAGE_LABELS = {
    "ingest": "Tải video",
    "transcribe": "Nhận dạng lời thoại",
    "ocr": "Đọc phụ đề (OCR)",
    "translate": "Dịch",
    "tts": "Lồng tiếng",
    "assemble": "Ráp video",
    "export": "Xuất video",
}


def _load_all_social_states() -> list:
    states = []
    for summary in sp.list_social_projects():
        try:
            states.append(sp.load_state(summary.id))
        except FileNotFoundError:
            continue
    return states


def _douyin_resting(state, now: datetime) -> bool:
    return state.douyin_backoff_until is not None and now < state.douyin_backoff_until


def _next_crawl_at(last_crawl_at: datetime | None) -> datetime | None:
    if last_crawl_at is None:
        return None
    offset_h = random.Random(last_crawl_at.timestamp()).uniform(-SOCIAL_CRAWL_JITTER_H, SOCIAL_CRAWL_JITTER_H)
    return last_crawl_at + timedelta(hours=SOCIAL_CRAWL_INTERVAL_H + offset_h)


def _running_job_suffix(suffix: str) -> list[str]:
    """social_id của các job `social:<id>:...<suffix>` đang chạy."""
    return [k.split(":")[1] for k in jobs.running_keys() if k.startswith("social:") and k.endswith(suffix)]


def _processing_items(states: list) -> list[tuple]:
    return [(st, it) for st in states for it in st.queue if it.status == QueueItemStatus.processing]


def _recover_stuck_items(states: list) -> bool:
    """Đánh dấu lỗi các video `processing` không còn pipeline nào chạy. Trả
    True nếu có sửa gì (nơi gọi nạp lại state)."""
    changed = False
    now_ts = time.time()
    alive: set[str] = set()
    for st, it in _processing_items(states):
        key = f"{st.id}:{it.aweme_id}"
        alive.add(key)
        if it.project_id and jobs.any_job_running(f"{it.project_id}:"):
            _stuck_since.pop(key, None)
            continue
        first = _stuck_since.setdefault(key, now_ts)
        if now_ts - first < SOCIAL_STUCK_GRACE_S:
            continue
        try:
            with sp.locked_state(st.id) as s:
                for i in s.queue:
                    if i.aweme_id == it.aweme_id and i.status == QueueItemStatus.processing:
                        i.status = QueueItemStatus.failed
                        i.error = "Pipeline bị gián đoạn (có thể do server khởi động lại) — bấm thử lại"
                        i.failed_stage = "activate"
            logger.warning("social scheduler: video '{}' của '{}' kẹt ở processing — đã đánh dấu lỗi", it.aweme_id, st.id)
            changed = True
        except FileNotFoundError:
            pass
        _stuck_since.pop(key, None)
    for key in list(_stuck_since):
        if key not in alive:
            _stuck_since.pop(key, None)

    # Video `ready` mà file thành phẩm không còn (project pipeline bị xoá tay)
    # → không bao giờ đăng được: đăng tay bị từ chối, bộ lập lịch thử lại mãi
    # mỗi phút, và nó vẫn chiếm 1 chỗ trong giới hạn video sẵn sàng. Chuyển về
    # lỗi ở bước kích hoạt để xử lý lại từ đầu được.
    for st in states:
        for it in st.queue:
            if it.status != QueueItemStatus.ready:
                continue
            if it.project_id and (pj.project_dir(it.project_id) / "export" / "final.mp4").exists():
                continue
            try:
                with sp.locked_state(st.id) as s:
                    for i in s.queue:
                        if i.aweme_id == it.aweme_id and i.status == QueueItemStatus.ready:
                            i.status = QueueItemStatus.failed
                            i.error = "Không còn file video đã xuất (project pipeline đã bị xoá) — bấm thử lại để xử lý lại"
                            i.failed_stage = "activate"
                logger.warning("social scheduler: video sẵn sàng '{}' của '{}' mất file xuất — đã đánh dấu lỗi", it.aweme_id, st.id)
                changed = True
            except FileNotFoundError:
                pass
    return changed


def _video_age_key(item) -> tuple:
    """Sắp video theo NGÀY ĐĂNG TRÊN DOUYIN (cũ trước), không theo lúc app
    crawl thấy: mã aweme_id của Douyin tăng dần theo thời gian đăng. Trước đây
    sắp theo `discovered_at` — nhưng mỗi lần crawl Douyin trả video MỚI NHẤT
    trước và app ghi `discovered_at` tăng dần theo đúng thứ tự đó, nên trong
    cùng 1 lần crawl video mới nhất lại bị coi là "cũ nhất" (đã xác nhận trên
    dữ liệu pokemon). So sánh theo (độ dài, chuỗi) để khỏi đổi sang số."""
    aid = item.aweme_id or ""
    if aid.isdigit():
        return (0, len(aid), aid)
    return (1, 0, item.discovered_at.isoformat())


def _posted_today(st, today) -> int:
    return sum(
        1 for i in st.queue if i.status == QueueItemStatus.posted and i.posted_at and i.posted_at.date() == today
    )


def _project_order_key(st) -> tuple:
    """Thứ tự cố định của các dự án trong lượt chạy mỗi ngày: dự án tạo trước
    chạy trước."""
    return (st.created_at, st.id)


def _pipeline_plan(states: list, now: datetime) -> tuple[list[dict], list[dict]]:
    """Kế hoạch xử lý trong ngày, theo THỨ TỰ DỰ ÁN (người dùng chốt): làm đủ
    số video cần cho hôm nay của dự án A rồi mới sang dự án B; đăng thì chạy
    theo lịch riêng của từng dự án.

    Với mỗi dự án: cần hôm nay = posts_per_day − số bài đã đăng hôm nay. Lượt
    1: lần lượt từng dự án, xử lý tới khi số video sẵn sàng đủ "cần hôm nay".
    Mọi dự án đủ rồi thì lượt 2: chuẩn bị trước cho ngày mai (thêm tối đa
    posts_per_day video), cũng lần lượt theo thứ tự dự án.

    Dự án có video LỖI thì DỪNG xử lý (người dùng chốt) cho tới khi người dùng
    thử lại hoặc bỏ qua video đó — giữ đúng thứ tự đăng tuyệt đối; hàng đợi
    chuyển sang dự án khác trong lúc chờ.

    Trả về (danh sách được xử lý theo đúng thứ tự tới lượt, danh sách mọi dự
    án kèm trạng thái trong ngày để hiển thị)."""
    today = now.date()
    daily: list[dict] = []
    pass1: list[dict] = []
    pass2: list[dict] = []
    for order, st in enumerate(sorted(states, key=_project_order_key), start=1):
        pending = sorted((i for i in st.queue if i.status == QueueItemStatus.pending), key=_video_age_key)
        ready = sum(1 for i in st.queue if i.status == QueueItemStatus.ready)
        processing = any(i.status == QueueItemStatus.processing for i in st.queue)
        failed = [i for i in st.queue if i.status == QueueItemStatus.failed]
        ppd = max(st.posts_per_day, 1)
        posted_today = _posted_today(st, today)
        need_today = max(0, ppd - posted_today)
        entry = {
            "order": order,
            "social_id": st.id,
            "social_title": st.title,
            "aweme_id": pending[0].aweme_id if pending else None,
            "video_title": (pending[0].title if pending else "") or "",
            "duration_sec": pending[0].duration_sec if pending else None,
            "posts_per_day": ppd,
            "posted_today": posted_today,
            "need_today": need_today,
            "ready_count": ready,
            "pending_count": len(pending),
            "failed_count": len(failed),
            "next_post_at": st.next_post_at,
            "status": "",
            "reason": None,
        }
        if st.status != "active":
            entry["status"], entry["reason"] = "paused", "Dự án đang tạm dừng"
        elif processing:
            entry["status"] = "processing"
        elif failed:
            entry["status"] = "stopped_failed"
            entry["reason"] = f"Có {len(failed)} video lỗi — thử lại hoặc bỏ qua để dự án chạy tiếp"
        elif posted_today >= ppd:
            entry["status"] = "done_today"
        elif ready >= need_today:
            entry["status"] = "prepared"
        elif _douyin_resting(st, now):
            entry["status"] = "resting"
            entry["reason"] = f"Đang nghỉ API Douyin tới {st.douyin_backoff_until:%H:%M %d/%m} (nghi risk-control)"
        elif not pending:
            entry["status"], entry["reason"] = "no_pending", "Hết video chờ xử lý — chờ lần crawl tới"
        else:
            entry["status"] = "waiting"
        daily.append(entry)

        can_process = (
            st.status == "active" and not processing and not failed and pending and not _douyin_resting(st, now)
        )
        if not can_process:
            continue
        if ready < need_today:
            pass1.append(entry)
        elif ready < need_today + ppd:
            pass2.append({**entry, "ahead": True})
    return pass1 + pass2, daily


def _publish_plan(states: list, now: datetime) -> list[dict]:
    """Lịch đăng của mọi dự án đang chạy, sắp theo giờ hẹn (sớm nhất trước).
    Đăng đủ posts_per_day bài trong ngày thì dừng tới hôm sau."""
    plan: list[dict] = []
    in_window = _in_posting_window(now)
    today = now.date()
    for st in states:
        if st.status != "active":
            continue
        ready = sorted((i for i in st.queue if i.status == QueueItemStatus.ready), key=_video_age_key)
        posted_today = _posted_today(st, today)
        due_time = st.next_post_at is None or now >= st.next_post_at
        if posted_today >= max(st.posts_per_day, 1):
            status = "done_today"
        elif not ready:
            status = "no_ready"
        elif not due_time:
            status = "scheduled"
        elif not in_window:
            status = "waiting_window"
        else:
            status = "due"
        plan.append({
            "social_id": st.id,
            "social_title": st.title,
            "next_post_at": st.next_post_at,
            "last_post_at": st.last_post_at,
            "posts_per_day": st.posts_per_day,
            "posted_today": posted_today,
            "ready_count": len(ready),
            "aweme_id": ready[0].aweme_id if ready else None,
            "video_title": (ready[0].title if ready else "") or "",
            "status": status,
        })
    plan.sort(key=lambda e: e["next_post_at"] or datetime.min)
    return plan


def _social_scheduler_tick() -> None:
    try:
        states = _load_all_social_states()
    except Exception:
        logger.exception("social scheduler: không đọc được danh sách dự án tự động")
        return
    now = datetime.now()

    if _recover_stuck_items(states):
        states = _load_all_social_states()

    # Tự dọn ổ đĩa (tự giới hạn tối đa mỗi 30 phút) — xem app/social_cleanup.py.
    if social_cleanup.maybe_run():
        states = _load_all_social_states()

    # Dự án cũ chưa có giờ hẹn đăng → tính bù 1 lần từ `last_post_at`.
    for st in states:
        if st.next_post_at is None and st.last_post_at is not None:
            try:
                with sp.locked_state(st.id) as s_next:
                    if s_next.next_post_at is None:
                        s_next.next_post_at = _compute_next_post_at(s_next.last_post_at, s_next.posts_per_day)
                    st.next_post_at = s_next.next_post_at
            except FileNotFoundError:
                pass

    active = [st for st in states if st.status == "active"]

    # 1) Crawl — 1 lượt tại 1 thời điểm cho cả hệ thống, dự án lâu chưa
    # crawl nhất được ưu tiên.
    if not _running_job_suffix(":crawl"):
        due = [st for st in active if not _douyin_resting(st, now) and _crawl_due(st.last_crawl_at, now)]
        due.sort(key=lambda st: st.last_crawl_at or datetime.min)
        for st in due[:1]:
            try:
                _start_social_crawl(st.id)
            except Exception:
                logger.exception("social scheduler: crawl lỗi cho '{}'", st.id)

    # 2) Xử lý video — chỉ khi KHÔNG có video nào đang xử lý ở BẤT KỲ dự án
    # nào (kể cả dự án đang tạm dừng: video của nó vẫn đang chiếm máy), và ổ
    # đĩa còn đủ chỗ (dưới ngưỡng thì chỉ đăng + dọn, không xử lý thêm).
    disk_low = social_cleanup.low_disk()
    if disk_low:
        logger.warning(
            "social scheduler: ổ đĩa còn {:.1f}GB (< {:.0f}GB) — tạm ngừng xử lý video mới",
            social_cleanup.free_gb(), social_cleanup.MIN_FREE_GB,
        )
    if not disk_low and not _processing_items(states):
        eligible, _ = _pipeline_plan(states, now)
        for entry in eligible:
            try:
                _start_social_activate(entry["social_id"], entry["aweme_id"])
                logger.info(
                    "social scheduler: bắt đầu xử lý video '{}' của '{}' (hàng đợi chung)",
                    entry["aweme_id"], entry["social_id"],
                )
                break
            except ValueError as err:
                # Lỗi tiền kiểm tra (vd video không có link tải) — đánh dấu
                # lỗi luôn, nếu không tick nào cũng chọn lại đúng video này và
                # chặn cả hàng đợi chung.
                logger.warning("social scheduler: không kích hoạt được '{}' ({}): {}", entry["aweme_id"], entry["social_id"], err)
                try:
                    with sp.locked_state(entry["social_id"]) as s:
                        for i in s.queue:
                            if i.aweme_id == entry["aweme_id"] and i.status == QueueItemStatus.pending:
                                i.status = QueueItemStatus.failed
                                i.error = str(err)
                                i.failed_stage = "activate"
                except FileNotFoundError:
                    pass
            except Exception:
                logger.exception("social scheduler: kích hoạt lỗi cho '{}'", entry["social_id"])
                break

    # 3) Đăng bài — 1 bài tại 1 thời điểm, chỉ trong khung giờ đăng, dự án
    # trễ giờ hẹn lâu nhất được đăng trước.
    if not _running_job_suffix(":publish"):
        for entry in (e for e in _publish_plan(active, now) if e["status"] == "due"):
            try:
                if _start_social_publish(entry["social_id"], entry["aweme_id"]) is not None:
                    break
            except ValueError as err:
                logger.warning("social scheduler: đăng bài lỗi cho '{}': {}", entry["social_id"], err)
            except Exception:
                logger.exception("social scheduler: đăng bài lỗi cho '{}'", entry["social_id"])


@app.get("/api/cleanup")
def cleanup_overview_route():
    """Trang "Tự dọn ổ đĩa": dung lượng trống + lịch tự dọn đầy đủ."""
    return {
        "free_gb": round(social_cleanup.free_gb(), 1),
        "min_free_gb": social_cleanup.MIN_FREE_GB,
        "low_disk": social_cleanup.low_disk(),
        "cleanup_after_hours": int(social_cleanup.CLEANUP_AFTER.total_seconds() // 3600),
        "last_cleanup": social_cleanup.last_freed_summary(),
        "plan": social_cleanup.cleanup_plan(),
    }


@app.post("/api/cleanup/keep")
def cleanup_keep_route(body: dict):
    """Loại 1 project khỏi tự dọn (keep=true) hoặc cho phép tự dọn lại."""
    project_id = str(body.get("project_id") or "").strip()
    if not project_id or not pj.project_dir(project_id).is_dir():
        raise HTTPException(status_code=404, detail="Không tìm thấy project")
    social_cleanup.set_keep(project_id, bool(body.get("keep", True)))
    return {"status": "ok", "project_id": project_id, "keep": bool(body.get("keep", True))}


@app.post("/api/cleanup/delete")
def cleanup_delete_route(body: dict):
    """Xoá ngay nhiều project đã chọn — mode "files" (chỉ file nặng) hoặc
    "project" (xoá hẳn project pipeline). Trả kết quả từng project."""
    ids = [str(x) for x in (body.get("project_ids") or []) if str(x).strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="Chưa chọn project nào")
    try:
        results = social_cleanup.delete_now(ids, str(body.get("mode") or "files"))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {
        "results": results,
        "deleted": sum(1 for r in results if r["ok"]),
        "freed_mb": sum(r.get("freed_mb", 0) for r in results if r["ok"]),
    }


@app.get("/api/social/{social_id}/cleanup-plan")
def social_cleanup_plan_route(social_id: str):
    """Lịch tự dọn file của các video trong 1 dự án tự động, theo aweme_id —
    chỉ bản xử lý hiện hành của từng video (bản cũ xem ở màn giám sát)."""
    plan = social_cleanup.cleanup_plan(use_cache=True)
    return {
        e["aweme_id"]: {
            "due_at": e["due_at"],
            "size_mb": e["size_mb"],
            "rule_label": e["rule_label"],
            "protected_reason": e["protected_reason"],
            "project_id": e["project_id"],
            "kept_by_user": e["kept_by_user"],
        }
        for e in plan
        if e["social_id"] == social_id and e["is_current"]
    }


@app.get("/api/social-monitor")
def social_monitor_route():
    """Màn giám sát: đang xử lý/đăng/crawl gì, thứ tự sắp xử lý, lịch đăng."""
    now = datetime.now()
    states = _load_all_social_states()
    running = jobs.running_keys()

    processing = []
    for st, it in _processing_items(states):
        stage = None
        progress = None
        if it.project_id:
            keys = [k for k in running if k.startswith(f"{it.project_id}:")]
            main_keys = [k for k in keys if k.split(":", 1)[1] in _STAGE_LABELS] or keys
            if main_keys:
                stage = main_keys[0].split(":", 1)[1]
                job = jobs.get_job(main_keys[0])
                if job is not None:
                    progress = {"done": job.done_count, "total": job.total, "label": job.current_label}
        processing.append({
            "social_id": st.id,
            "social_title": st.title,
            "aweme_id": it.aweme_id,
            "video_title": it.title or "",
            "project_id": it.project_id,
            "duration_sec": it.duration_sec,
            "stage": stage,
            "stage_label": _STAGE_LABELS.get(stage or "", stage or "Đang chờ bước tiếp theo"),
            "progress": progress,
        })

    titles = {st.id: st.title for st in states}

    def _job_rows(suffix: str) -> list[dict]:
        rows = []
        for key in running:
            if key.startswith("social:") and key.endswith(suffix):
                sid = key.split(":")[1]
                job = jobs.get_job(key)
                rows.append({
                    "social_id": sid,
                    "social_title": titles.get(sid, sid),
                    "aweme_id": key.split(":")[2] if key.count(":") >= 3 else None,
                    "label": job.current_label if job else None,
                })
        return rows

    process_queue, daily_plan = _pipeline_plan(states, now)
    publish_plan = _publish_plan(states, now)
    all_items = [it for st in states for it in st.queue]
    today = now.date()
    return {
        "now": now,
        "in_posting_window": _in_posting_window(now),
        "posting_windows": [f"{a}h-{b}h" for a, b in SOCIAL_POSTING_WINDOWS],
        "processing": processing,
        "publishing": _job_rows(":publish"),
        "crawling": _job_rows(":crawl"),
        "pipeline_queue": process_queue,
        "daily_plan": daily_plan,
        "storage": {
            "free_gb": round(social_cleanup.free_gb(), 1),
            "min_free_gb": social_cleanup.MIN_FREE_GB,
            "low_disk": social_cleanup.low_disk(),
            "cleanup_after_hours": int(social_cleanup.CLEANUP_AFTER.total_seconds() // 3600),
            "last_cleanup": social_cleanup.last_freed_summary(),
            # Chỉ tóm tắt — bảng đầy đủ ở trang "Tự dọn ổ đĩa" (/api/cleanup).
            "plan_summary": (lambda plan: {
                "due_count": sum(1 for e in plan if e["due_at"]),
                "due_mb": sum(e["size_mb"] for e in plan if e["due_at"]),
                "kept_count": sum(1 for e in plan if not e["due_at"]),
            })(social_cleanup.cleanup_plan(now, use_cache=True)),
        },
        "publish_plan": publish_plan,
        "crawl_plan": sorted(
            [
                {
                    "social_id": st.id,
                    "social_title": st.title,
                    "last_crawl_at": st.last_crawl_at,
                    "next_crawl_at": _next_crawl_at(st.last_crawl_at),
                    "active": st.status == "active",
                }
                for st in states
            ],
            key=lambda e: e["next_crawl_at"] or datetime.min,
        ),
        "stats": {
            "projects": len(states),
            "projects_active": sum(1 for st in states if st.status == "active"),
            "pending": sum(1 for i in all_items if i.status == QueueItemStatus.pending),
            "processing": sum(1 for i in all_items if i.status == QueueItemStatus.processing),
            "ready": sum(1 for i in all_items if i.status == QueueItemStatus.ready),
            "failed": sum(1 for i in all_items if i.status == QueueItemStatus.failed),
            "posted_today": sum(
                1 for i in all_items if i.status == QueueItemStatus.posted and i.posted_at and i.posted_at.date() == today
            ),
        },
    }


def _social_scheduler_loop() -> None:
    while True:
        try:
            _social_scheduler_tick()
        except Exception:
            logger.exception("social scheduler: lỗi không mong đợi ở 1 tick")
        time.sleep(SOCIAL_SCHEDULER_TICK_S)


@app.on_event("startup")
def _start_social_scheduler() -> None:
    threading.Thread(target=_social_scheduler_loop, daemon=True).start()


@app.get("/api/projects/{project_id}/assets/{asset_path:path}")
def project_asset(project_id: str, asset_path: str):
    root = pj.project_dir(project_id).resolve()
    target = (root / asset_path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(status_code=403, detail="Đường dẫn không hợp lệ")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy file")
    return FileResponse(target)


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str = ""):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(FRONTEND_DIST / "index.html")
