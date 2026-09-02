from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger

from . import jobs, projects as pj, settings as app_settings
from .models import StageRecord, StageStatus
from .schemas import (
    AppSettingsResponse,
    CreateProjectRequest,
    IngestUrlRequest,
    JobItemResponse,
    JobStatusResponse,
    LogEntry,
    ProjectDetailResponse,
    ProjectSummary,
    RenameProjectRequest,
    SrtCue,
    StartTranscribeRequest,
    StartTTSRequest,
    TTSCueRequest,
    TTSManifestEntryResponse,
    UpdateAppSettingsRequest,
    UpdateCueRequest,
)
from .stages import assemble as assemble_stage
from .stages import dub_audio as dub_audio_stage
from .stages import fetch_url as fetch_url_stage
from .stages import ingest as ingest_stage
from .stages import transcribe as transcribe_stage
from .stages import transcribe_sensevoice as sensevoice_stage
from .stages import translate as translate_stage
from .stages import tts as tts_stage
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
    )


def _require_done(state, stage: str, message: str) -> None:
    rec = state.stages.get(stage)
    if rec is None or rec.status != StageStatus.done:
        raise HTTPException(status_code=409, detail=message)


# ------------------------------------------------------------------ Settings


@app.get("/api/settings", response_model=AppSettingsResponse)
def get_settings_route():
    return app_settings.get_settings()


@app.put("/api/settings", response_model=AppSettingsResponse)
def update_settings_route(body: UpdateAppSettingsRequest):
    return app_settings.update_settings(
        workspace_dir=body.workspace_dir,
        gemini_api_key=body.gemini_api_key,
        gemini_model=body.gemini_model,
        whisper_model=body.whisper_model,
        whisper_device=body.whisper_device,
        whisper_language=body.whisper_language,
    )


# ------------------------------------------------------------------ Projects


@app.get("/api/projects", response_model=list[ProjectSummary])
def list_projects_route():
    return [_summary(s) for s in pj.list_projects()]


@app.post("/api/projects", response_model=ProjectSummary, status_code=201)
def create_project_route(body: CreateProjectRequest):
    try:
        state = pj.create_project(body.title)
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


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project_route(project_id: str):
    if any(jobs.is_job_running(f"{project_id}:{s}") for s in ("ingest", "transcribe", "translate", "tts", "assemble")):
        raise HTTPException(status_code=409, detail="Đang có job chạy — đợi xong đã")
    try:
        pj.delete_project(project_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@app.get("/api/projects/{project_id}", response_model=ProjectDetailResponse)
def project_detail(project_id: str):
    try:
        state = pj.load_project(project_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    root = pj.project_dir(project_id)
    zh_cues = load_srt(root / "sub_zh.srt")
    vi_cues = {c.id: c.text for c in load_srt(root / "sub_vi.srt")}
    cues = [
        SrtCue(id=c.id, start=c.start, end=c.end, text=c.text, text_vi=vi_cues.get(c.id))
        for c in zh_cues
    ]
    entity: dict[str, str] = {}
    dict_path = root / "entity_dict.json"
    if dict_path.exists():
        raw = json.loads(dict_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            entity = {str(k): str(v) for k, v in raw.items()}

    video_url = None
    if state.video_relpath:
        video_url = f"/api/projects/{project_id}/assets/{state.video_relpath}"

    sub_zh = (root / "sub_zh.srt").read_text(encoding="utf-8") if (root / "sub_zh.srt").exists() else None
    sub_vi = (root / "sub_vi.srt").read_text(encoding="utf-8") if (root / "sub_vi.srt").exists() else None

    tts_manifest: list[dict] = []
    manifest_path = root / "audio" / "manifest.json"
    if manifest_path.exists():
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(raw_manifest, list):
            tts_manifest = raw_manifest

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


# ------------------------------------------------------------------ Ingest


def _finalize_ingest(project_id: str, root: Path, dest: Path, original: str, duration: float | None, log_msg: str) -> None:
    rel = dest.name
    for leftover in ("sub_zh.srt", "sub_vi.srt", "entity_dict.json", "background.wav"):
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
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            dest, original, duration = fetch_url_stage.download_video_from_share(root, share_url, on_progress=on_progress)
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


@app.post("/api/projects/{project_id}/transcribe", status_code=202)
def start_transcribe(project_id: str, body: StartTranscribeRequest | None = None):
    state = pj.load_project(project_id)
    _require_done(state, "ingest", "Chưa upload video")
    if not state.video_relpath:
        raise HTTPException(status_code=409, detail="Chưa có file video")
    root = pj.project_dir(project_id)
    video_path = root / state.video_relpath
    total = max(1, int(state.duration_sec or 1))

    engine = body.engine if body and body.engine == "sensevoice" else "whisper"
    stage_fn = sensevoice_stage.transcribe_video if engine == "sensevoice" else transcribe_stage.transcribe_video
    engine_label = "SenseVoice" if engine == "sensevoice" else "Whisper zh"

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
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            cues, lang = stage_fn(video_path, root / "sub_zh.srt", on_progress=on_progress)
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

    job = jobs.start_job(f"{project_id}:transcribe", total, target)
    if job is None:
        raise HTTPException(status_code=409, detail=f"{engine_label} đang chạy cho dự án này")
    return {"status": "started"}


@app.post("/api/projects/{project_id}/translate", status_code=202)
def start_translate(project_id: str):
    state = pj.load_project(project_id)
    _require_done(state, "transcribe", "Chưa có phụ đề tiếng Trung — chạy Whisper trước")
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
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            _, _, entity = translate_stage.translate_project(root, on_progress=on_progress)
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

    job = jobs.start_job(f"{project_id}:translate", n, target)
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
    if voice not in {v["id"] for v in tts_stage.VOICES}:
        voice = tts_stage.DEFAULT_VOICE
    try:
        entry = tts_stage.tts_single_segment(root, cue_id, voice=voice)
    except tts_stage.TTSError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    pj.append_log(project_id, "tts", f"Đọc lại câu #{cue_id}")
    return TTSManifestEntryResponse(**entry)


@app.get("/api/tts/preview")
def tts_preview(voice: str = ""):
    voice = voice if voice in {v["id"] for v in tts_stage.VOICES} else tts_stage.DEFAULT_VOICE
    try:
        audio_bytes = tts_stage.preview_voice(voice)
    except tts_stage.TTSError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return Response(content=audio_bytes, media_type="audio/mpeg")


@app.post("/api/projects/{project_id}/tts", status_code=202)
def start_tts(project_id: str, body: StartTTSRequest | None = None):
    state = pj.load_project(project_id)
    _require_done(state, "translate", "Chưa có bản dịch tiếng Việt — chạy Gemini trước")
    root = pj.project_dir(project_id)

    voice = body.voice if body and body.voice else ""
    if voice not in {v["id"] for v in tts_stage.VOICES}:
        voice = tts_stage.DEFAULT_VOICE
    voice_label = next((v["label"] for v in tts_stage.VOICES if v["id"] == voice), voice)
    retry_only = bool(body and body.retry_failed_only)

    if retry_only:
        manifest_path = root / "audio" / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=409, detail="Chưa chạy TTS lần nào")
        prev_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        n = sum(1 for m in prev_manifest if m.get("error")) or 1
    else:
        n = len(load_srt(root / "sub_vi.srt")) or 1

    def target(job: jobs.JobState) -> None:
        label = f"Thử lại câu lỗi · {voice_label}" if retry_only else f"CapCut TTS · {voice_label}"
        job.items = [jobs.JobItem(id="tts", label=label)]
        job.items[0].status = "running"
        job.current_label = voice_label
        with pj.locked_project(project_id) as s:
            pj.reset_from(s, "assemble")
            s.stages["tts"].status = StageStatus.running
            s.stages["tts"].error = None

        def on_progress(done: int, tot: int, label: str) -> None:
            job.done_count = done
            job.total = max(tot, 1)
            job.current_label = label

        try:
            if retry_only:
                _, manifest = tts_stage.retry_failed_segments(root, voice=voice, on_progress=on_progress)
            else:
                _, manifest = tts_stage.tts_project(root, voice=voice, on_progress=on_progress)
        except tts_stage.TTSError as err:
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
            s.stages["tts"].error = None
            s.stages["tts"].at = datetime.now()
        pj.append_log(project_id, "tts", f"{ok}/{len(manifest)} câu · {voice_label}")

    job = jobs.start_job(f"{project_id}:tts", n, target)
    if job is None:
        raise HTTPException(status_code=409, detail="TTS đang chạy cho dự án này")
    return {"status": "started"}


# ------------------------------------------------------------------ Assemble (CapCut draft)


@app.post("/api/projects/{project_id}/assemble", status_code=202)
def start_assemble(project_id: str):
    state = pj.load_project(project_id)
    _require_done(state, "tts", "Chưa có audio TTS — chạy TTS trước")
    root = pj.project_dir(project_id)
    if not state.video_relpath:
        raise HTTPException(status_code=409, detail="Chưa có file video")

    def target(job: jobs.JobState) -> None:
        job.items = [
            jobs.JobItem(id="background", label="Tách nhạc nền (demucs)"),
            jobs.JobItem(id="draft", label="Dựng draft CapCut"),
        ]
        job.current_label = "Tách nhạc nền"
        with pj.locked_project(project_id) as s:
            s.stages["assemble"].status = StageStatus.running
            s.stages["assemble"].error = None

        video_path = root / state.video_relpath
        bg_path = root / "background.wav"

        def on_bg_progress(done: int, tot: int, label: str) -> None:
            job.current_label = label

        if not bg_path.exists():
            job.items[0].status = "running"
            try:
                dub_audio_stage.extract_background(video_path, bg_path, on_progress=on_bg_progress)
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
            )
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
        pj.append_log(project_id, "assemble", f"draft → {draft_path}")

    job = jobs.start_job(f"{project_id}:assemble", 1, target)
    if job is None:
        raise HTTPException(status_code=409, detail="Assemble đang chạy cho dự án này")
    return {"status": "started"}


@app.get("/api/projects/{project_id}/jobs/{stage}", response_model=JobStatusResponse)
def job_status(project_id: str, stage: str):
    if stage not in ("ingest", "transcribe", "translate", "tts", "assemble"):
        raise HTTPException(status_code=404, detail="Stage không hỗ trợ job")
    job = jobs.get_job(f"{project_id}:{stage}")
    if job is None:
        state = pj.load_project(project_id)
        orphaned = state.stages[stage].status == StageStatus.running
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
