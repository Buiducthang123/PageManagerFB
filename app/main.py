from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger

from . import config, downloads as dl, jobs, merges as mg, projects as pj, settings as app_settings
from .models import Episode, StageRecord, StageStatus
from .schemas import (
    AppSettingsResponse,
    CreateDownloadRequest,
    CreateEpisodeRequest,
    CreateProjectRequest,
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
)
from .stages import assemble as assemble_stage
from .stages import douyin_dl as douyin_dl_stage
from .stages import dub_audio as dub_audio_stage
from .stages import export_direct as export_direct_stage
from .stages import fetch_url as fetch_url_stage
from .stages import ingest as ingest_stage
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
DEFAULT_TRANSCRIBE_ENGINE = "whisper"


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
        _start_transcribe(project_id, state.auto_engine)
    elif finished_stage == "transcribe":
        _start_translate(project_id)
    elif finished_stage == "translate":
        _start_tts(project_id, state.auto_voice, engine=state.auto_tts_engine)
    elif finished_stage == "tts":
        _start_assemble(project_id, audio_mode=state.auto_audio_mode, min_video_speed=state.auto_min_video_speed)


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
                project_id, episode_id, audio_mode=state.auto_audio_mode, min_video_speed=state.auto_min_video_speed
            )
        elif pj.all_episodes_stage_done(state, "tts"):
            _start_assemble(project_id, audio_mode=state.auto_audio_mode, min_video_speed=state.auto_min_video_speed)
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
    audio_mode = body.audio_mode if body.audio_mode in ("separated", "original", "mute") else "separated"
    with pj.locked_project(project_id) as state:
        state.auto_pipeline = body.enabled
        state.auto_engine = engine
        state.auto_tts_engine = tts_engine
        state.auto_voice = body.voice if body.voice in {v["id"] for v in _tts_voices(tts_engine)} else ""
        state.auto_audio_mode = audio_mode
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
    project_id: str, episode_id: str, audio_mode: str = "separated", min_video_speed: float = 0.85
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
        audio_mode=(body.audio_mode if body else "separated"),
        min_video_speed=(body.min_video_speed if body else 0.85),
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

        try:
            cues, lang = stage_fn(video_path, root / "sub_zh.srt", on_progress=on_progress, **extra_kwargs)
        except jobs.JobCancelled:
            _mark_cancelled(project_id, "transcribe", job)
            return
        except Exception as err:
            logger.exception("{} lỗi {}", engine_label, project_id)
            job.items[0].status = "failed"
            job.items[0].error = str(err)
            job.status = "failed"
            job.error = str(err)
            with pj.locked_project(project_id) as s:
                s.stages["transcribe"].status = StageStatus.failed
                s.stages["transcribe"].error = str(err)
            pj.append_log(project_id, "transcribe", f"Lỗi: {err}")
            return

        job.items[0].status = "done"
        job.done_count = job.total
        job.status = "done"
        with pj.locked_project(project_id) as s:
            s.stages["transcribe"].status = StageStatus.done
            s.stages["transcribe"].output = "sub_zh.srt"
            s.stages["transcribe"].progress = f"{len(cues)} câu · {lang}"
            s.stages["transcribe"].error = None
            s.stages["transcribe"].engine = engine
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


def _start_assemble_multi(project_id: str, state, audio_mode: str, min_video_speed: float = 0.85) -> jobs.JobState | None:
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
        pj.append_log(project_id, "assemble", f"draft → {draft_path} ({len(episodes)} tập)")

    return jobs.start_job(f"{project_id}:assemble", 1, target)


def _start_assemble(project_id: str, audio_mode: str = "separated", min_video_speed: float = 0.85) -> jobs.JobState | None:
    state = pj.load_project(project_id)
    root = pj.project_dir(project_id)

    if state.project_type == "multi":
        return _start_assemble_multi(project_id, state, audio_mode, min_video_speed)

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
        pj.append_log(project_id, "assemble", f"draft → {draft_path}")

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


def _export_dir(project_id: str) -> Path:
    d = pj.project_dir(project_id) / "export"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _export_asset_path(project_id: str, stem: str) -> Path | None:
    """Tìm file `music.*`/`logo.*` đã upload trong thư mục export của
    project (đuôi file có thể khác nhau tuỳ định dạng người dùng upload)."""
    matches = sorted(_export_dir(project_id).glob(f"{stem}.*"))
    return matches[0] if matches else None


def _start_export(project_id: str, audio_mode: str, min_video_speed: float) -> jobs.JobState | None:
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
                return
        job.items[0].status = "done"

        job.items[1].status = "running"
        job.current_label = "Dựng video"

        def on_render_progress(done: int, tot: int, label: str) -> None:
            job.raise_if_cancelled()
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        blur_region = tuple(state.export_blur_region) if state.export_blur_region else None
        music_path = _export_asset_path(project_id, "music")
        logo_path = _export_asset_path(project_id, "logo")
        output_path = _export_dir(project_id) / "final.mp4"

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
                music_path=music_path,
                logo_path=logo_path,
                min_video_speed=min_video_speed,
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
        audio_mode=(body.audio_mode if body else "separated"),
        min_video_speed=(body.min_video_speed if body else 0.85),
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
        audio_mode=(body.audio_mode if body else "separated"),
        min_video_speed=(body.min_video_speed if body else 0.85),
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
