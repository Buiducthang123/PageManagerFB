from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from .. import config
from ..utils.srt import Cue, load_srt, update_cue_text, write_srt

FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "entity_dict": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "text_vi": {"type": "string"},
                },
                "required": ["id", "text_vi"],
            },
        },
    },
    "required": ["items"],
}


class LLMNotConfigured(RuntimeError):
    pass


class GeminiQuotaError(RuntimeError):
    """Hết quota — phân biệt Free Tier theo ngày vs rate-limit tạm."""


def _api_key() -> str:
    import os

    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise LLMNotConfigured(
            "Chưa cấu hình GEMINI_API_KEY — mở Cài đặt và dán key từ aistudio.google.com."
        )
    return key


def _err_text(err: BaseException) -> str:
    return str(err)


def _is_daily_free_tier(err: BaseException) -> bool:
    s = _err_text(err)
    return "free_tier" in s.lower() or "FreeTier" in s or "PerDayPerProjectPerModel-FreeTier" in s


def _retry_after_s(err: BaseException) -> Optional[float]:
    if _is_daily_free_tier(err):
        return None
    m = re.search(r"retry in ([\d.]+)\s*s", _err_text(err), re.I)
    if m:
        return min(float(m.group(1)) + 1.0, 120.0)
    s = _err_text(err).lower()
    if "429" in s or "resource_exhausted" in s:
        return 30.0
    if any(tok in s for tok in ("503", "unavailable", "high demand", "try again")):
        return 8.0
    return None


def _parse_json(text: str):
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    return json.loads(raw)


def _normalize_entities(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key in ("items", "entity_dict"):
            continue
        if isinstance(value, dict):
            vi = value.get("vi") or value.get("text_vi") or value.get("name_vi")
        else:
            vi = value
        if key and vi:
            out[str(key)] = str(vi)
    return out


def _quota_message(err: BaseException) -> str:
    model = config.resolve_gemini_model()
    return (
        f"Gemini báo Free Tier (20 request/ngày cho {model}). "
        "Gói Gemini App / Google One AI Pro không nâng quota API. "
        "Cần bật Billing trên Google Cloud project của đúng API key: "
        "https://aistudio.google.com/usage — rồi tạo lại key từ project đã gắn thẻ. "
        f"Chi tiết: {_err_text(err)[:400]}"
    )


def _generate(prompt: str, schema: dict | None = None) -> Any:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_api_key())
    model = config.resolve_gemini_model()

    def _cfg(*, with_thinking: bool) -> types.GenerateContentConfig:
        kwargs: dict = {
            "response_mime_type": "application/json",
            "max_output_tokens": 32768,
        }
        if schema is not None:
            kwargs["response_json_schema"] = schema
        if not model.startswith("gemini-3"):
            kwargs["temperature"] = config.GEMINI_TEMPERATURE
        elif with_thinking:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="MINIMAL")
        return types.GenerateContentConfig(**kwargs)

    def call(with_thinking: bool):
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=_cfg(with_thinking=with_thinking),
        )
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            return parsed
        text = (response.text or "").strip()
        if not text:
            reason = None
            try:
                reason = response.candidates[0].finish_reason
            except Exception:
                pass
            raise ValueError(f"Gemini trả về rỗng (finish_reason={reason})")
        return _parse_json(text)

    last: BaseException | None = None
    thinking = True
    for attempt in range(4):
        try:
            return call(with_thinking=thinking)
        except Exception as err:
            last = err
            if _is_daily_free_tier(err):
                raise GeminiQuotaError(_quota_message(err)) from err
            msg = _err_text(err).lower()
            if thinking and model.startswith("gemini-3") and "thinking" in msg:
                thinking = False
                logger.warning("Bỏ thinking_config rồi gọi lại")
                continue
            wait = _retry_after_s(err)
            if wait is None or attempt == 3:
                raise
            logger.warning("Gemini retry sau {:.0f}s ({})", wait, err)
            time.sleep(wait)
    assert last is not None
    raise last


def _one_shot(cues: list[Cue]) -> tuple[list[dict], dict[str, str]]:
    payload = [{"id": c.id, "text": c.text} for c in cues]
    prompt = f"""Bạn là dịch giả chuyên nghiệp zh→vi cho video reup. Bản dịch sẽ được đọc thành
giọng nói (TTS) — văn phong phải TỰ NHIÊN khi đọc to, không dịch máy móc từng chữ.

Làm CẢ 3 việc trong 1 JSON:
1. entity_dict: tên riêng (người/địa danh/công ty/nickname) → phiên âm tiếng Việt nhất quán.
   Không có thì {{}}. Nếu câu nguồn có cụm rõ ràng bị nghe nhầm (lỗi ASR) và nghi là tên riêng —
   giữ dạng phiên âm hợp lý, TUYỆT ĐỐI KHÔNG dịch nghĩa đen thành từ thật không liên quan ngữ cảnh.
2. Clean từng câu: bỏ filler, sửa lỗi nghe nhầm rõ ràng (đặc biệt lỗi đồng âm tiếng Trung).
3. Dịch đủ MỌI câu, giữ đúng id, áp dụng entity_dict nhất quán cho MỌI lần tên đó xuất hiện.
   Giữ giọng điệu gốc (cảm thán, tiếng lóng bình luận game) nhưng làm mềm ngôn từ tục tĩu quá
   mức để phù hợp kiểm duyệt Facebook.

Câu nguồn:
{json.dumps(payload, ensure_ascii=False)}

Trả về: {{"entity_dict":{{"原名":"phiên âm"}},"items":[{{"id":1,"text_vi":"..."}}]}}"""
    data = _generate(prompt, schema=FULL_SCHEMA)
    if not isinstance(data, dict):
        raise ValueError("Gemini không trả JSON object")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("Gemini không trả danh sách items")
    entities = _normalize_entities(data.get("entity_dict") or {})
    return items, entities


def process_llm(
    cues: list[Cue],
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[list[Cue], dict[str, str]]:
    if on_progress:
        on_progress(0, len(cues), f"1 lần gọi · {len(cues)} câu")
    try:
        items, entity_dict = _one_shot(cues)
    except (json.JSONDecodeError, ValueError) as err:
        if len(cues) < 20:
            raise
        logger.warning("1 shot JSON lỗi ({}), tách 2 nửa", err)
        if on_progress:
            on_progress(0, len(cues), "tách 2 nửa")
        mid = len(cues) // 2
        a_items, a_ent = _one_shot(cues[:mid])
        time.sleep(2)
        b_items, b_ent = _one_shot(cues[mid:])
        items = a_items + b_items
        entity_dict = {**a_ent, **b_ent}

    translated_map: dict[int, str] = {}
    for item in items:
        try:
            translated_map[int(item["id"])] = str(item["text_vi"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
    logger.info("Gemini 1-shot: {} câu dịch, {} tên riêng", len(translated_map), len(entity_dict))

    out = [
        Cue(id=c.id, start=c.start, end=c.end, text=translated_map.get(c.id, c.text))
        for c in cues
    ]
    if on_progress:
        on_progress(len(cues), len(cues), "xong")
    return out, entity_dict


SINGLE_SCHEMA = {
    "type": "object",
    "properties": {"text_vi": {"type": "string"}},
    "required": ["text_vi"],
}


def _load_entity_dict(project_root: Path) -> dict[str, str]:
    dict_path = project_root / "entity_dict.json"
    if not dict_path.exists():
        return {}
    raw = json.loads(dict_path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def retranslate_cue(project_root: Path, cue_id: int) -> str:
    """Dịch lại đúng 1 câu — dùng lại entity_dict đã có, chỉ ghi đè câu đó
    trong sub_vi.srt, không đụng các câu khác (đỡ tốn quota Gemini)."""
    zh_cues = {c.id: c for c in load_srt(project_root / "sub_zh.srt")}
    cue = zh_cues.get(cue_id)
    if cue is None:
        raise ValueError(f"Không tìm thấy câu #{cue_id} trong sub_zh.srt")

    entity_dict = _load_entity_dict(project_root)
    prompt = f"""Bạn là dịch giả chuyên nghiệp zh→vi cho video reup. Bản dịch sẽ được đọc thành
giọng nói (TTS) — văn phong phải TỰ NHIÊN khi đọc to, không dịch máy móc từng chữ.

Entity dict — áp dụng nhất quán nếu câu có nhắc tên riêng liên quan:
{json.dumps(entity_dict, ensure_ascii=False)}

Dịch + clean câu nguồn sau (bỏ filler, sửa lỗi nghe nhầm rõ ràng nếu có):
{cue.text}

Trả về: {{"text_vi":"..."}}"""
    data = _generate(prompt, schema=SINGLE_SCHEMA)
    if not isinstance(data, dict) or not data.get("text_vi"):
        raise ValueError("Gemini không trả về text_vi")
    text_vi = str(data["text_vi"]).strip()

    vi_path = project_root / "sub_vi.srt"
    if load_srt(vi_path):
        update_cue_text(vi_path, cue_id, text_vi)
    return text_vi


def translate_project(
    project_root: Path,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[Path, Path, dict[str, str]]:
    zh_path = project_root / "sub_zh.srt"
    cues = load_srt(zh_path)
    if not cues:
        raise ValueError("Chưa có sub_zh.srt — chạy Whisper trước.")

    vi_cues, entity_dict = process_llm(cues, on_progress=on_progress)
    vi_path = project_root / "sub_vi.srt"
    dict_path = project_root / "entity_dict.json"
    write_srt(vi_path, vi_cues)
    dict_path.write_text(json.dumps(entity_dict, indent=2, ensure_ascii=False), encoding="utf-8")
    return vi_path, dict_path, entity_dict
