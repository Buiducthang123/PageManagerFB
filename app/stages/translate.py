from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from .. import config
from ..utils.srt import Cue, load_srt, parse_ts, update_cue_text, write_srt

# Tốc độ đọc tự nhiên mục tiêu / ngưỡng chấp nhận được (ký tự việt/giây).
# Đã thử nhét ngân sách thời lượng vào prompt dịch 1-lần-cho-cả-315-câu — cải
# thiện CPS thật nhưng làm Gemini áp 1 văn phong súc tích cho TOÀN BỘ phản
# hồi (kể cả câu không hề gấp), gây mất tên riêng/từ nối, đọc cộc lốc. Nên
# tách 2 lượt: dịch tự nhiên bình thường trước (không nhắc ngân sách gì cả),
# rồi CHỈ gom đúng những câu thật sự vượt MAX_CPS gửi riêng 1 lượt nén lại —
# giữ chất lượng tự nhiên cho phần lớn câu còn lại.
TARGET_CPS = 15.5
MAX_CPS = 18.0

# Video dài (90p-3h) có thể lên tới vài nghìn câu — dịch nguyên video trong 1
# lần gọi Gemini sẽ vỡ giới hạn max_output_tokens (32768, xem _generate).
# Chia thành từng nhóm nhỏ, gọi Gemini lặp lại theo vòng lặp thay vì 1 lần
# duy nhất cho cả video — không phụ thuộc độ dài video nữa. entity_dict được
# truyền nối tiếp qua từng nhóm (nhóm sau biết tên riêng nhóm trước đã chốt)
# để tên riêng vẫn nhất quán xuyên suốt dù dịch theo nhiều lượt gọi.
#
# BATCH_SIZE=80 (đã hạ từ 200): quan sát thực tế trên video dài — batch 200
# câu, chất lượng JSON Gemini trả về TỤT RÕ RỆT ở khoảng 15-20 câu cuối mỗi
# batch (id bị gán nhầm nội dung, hoặc bị bỏ sót hẳn khỏi JSON) dù JSON vẫn
# hợp lệ cú pháp — không phải lỗi parse nên nhánh retry ở _translate_batch
# không bắt được. Batch nhỏ hơn giảm hẳn tần suất gặp vùng "gần cuối danh
# sách dài" này. Kèm thêm bước tự phát hiện + dịch lại id bị thiếu ở
# process_llm bên dưới làm lưới an toàn thứ 2.
BATCH_SIZE = 80
BATCH_SLEEP_S = 2.0


def _duration_s(cue: Cue) -> float:
    return max(parse_ts(cue.end) - parse_ts(cue.start), 0.1)

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


def _one_shot(
    cues: list[Cue],
    known_entity_dict: dict[str, str] | None = None,
) -> tuple[list[dict], dict[str, str]]:
    payload = [{"id": c.id, "text": c.text} for c in cues]
    known_block = ""
    if known_entity_dict:
        known_block = f"""

Entity dict đã CHỐT từ các tập trước trong cùng series — áp dụng ĐÚNG NGUYÊN VĂN nếu tên riêng
xuất hiện lại, không tự đổi phiên âm khác đi. Chỉ bổ sung entity_dict mới cho tên riêng CHƯA có
trong danh sách này:
{json.dumps(known_entity_dict, ensure_ascii=False)}"""
    prompt = f"""Bạn là dịch giả chuyên nghiệp zh→vi cho video reup. Bản dịch sẽ được đọc thành
giọng nói (TTS) — văn phong phải TỰ NHIÊN khi đọc to, không dịch máy móc từng chữ.

Làm CẢ 3 việc trong 1 JSON:
1. entity_dict: tên riêng (người/địa danh/công ty/nickname) → phiên âm tiếng Việt nhất quán.
   Không có thì {{}}. Nếu câu nguồn có cụm rõ ràng bị nghe nhầm (lỗi ASR) và nghi là tên riêng —
   giữ dạng phiên âm hợp lý, TUYỆT ĐỐI KHÔNG dịch nghĩa đen thành từ thật không liên quan ngữ cảnh.
2. Clean từng câu: bỏ filler, sửa lỗi nghe nhầm rõ ràng (đặc biệt lỗi đồng âm tiếng Trung).
3. Dịch đủ MỌI câu, giữ đúng id, áp dụng entity_dict nhất quán cho MỌI lần tên đó xuất hiện —
   KHÔNG được bỏ tên riêng thay bằng đại từ/mô tả mơ hồ. Giữ giọng điệu gốc (cảm thán, tiếng lóng
   bình luận game) nhưng làm mềm ngôn từ tục tĩu quá mức để phù hợp kiểm duyệt Facebook.{known_block}

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


def _cps(text: str, duration_s: float) -> float:
    return len(text.replace(" ", "")) / duration_s


def _compact_rewrite_batch(
    items: list[tuple[Cue, str]],
    entity_dict: dict[str, str],
) -> dict[int, str]:
    """Gom đúng những câu vượt MAX_CPS, gửi riêng 1 lượt yêu cầu viết súc tích
    hơn theo ngân sách ký tự — chỉ đụng tới nhóm này, không ảnh hưởng các câu
    đã dịch tự nhiên tốt ở lượt 1."""
    payload = [
        {
            "id": cue.id,
            "zh": cue.text,
            "current_vi": vi_text,
            "available_duration_s": round(_duration_s(cue), 2),
            "max_chars_vi": max(round(_duration_s(cue) * TARGET_CPS), 1),
        }
        for cue, vi_text in items
    ]
    prompt = f"""Các câu tiếng Việt dưới đây ("current_vi") đọc TTS sẽ bị GẤP vì dài hơn
"available_duration_s" giây cho phép — cần viết lại NGẮN GỌN hơn, bám sát "max_chars_vi" ký tự
(tốc độ đọc tự nhiên ~{TARGET_CPS} ký tự/giây).

Cách làm: bỏ từ đệm/thừa/trạng từ không cần thiết, dùng từ/cụm ngắn hơn nghĩa tương đương, gộp ý
nếu được. TUYỆT ĐỐI KHÔNG:
- Bỏ tên riêng rồi thay bằng đại từ/mô tả mơ hồ (vd "cậu ấy", "người kia") — giữ nguyên tên.
- Viết cộc lốc thiếu ngữ pháp hay khó hiểu — vẫn phải là câu tiếng Việt tự nhiên, nghe xuôi tai.
Nếu không thể rút ngắn thêm mà vẫn giữ tự nhiên, giữ nguyên current_vi.

Entity dict — áp dụng nhất quán, không đổi tên khác đi:
{json.dumps(entity_dict, ensure_ascii=False)}

Danh sách câu cần viết lại:
{json.dumps(payload, ensure_ascii=False)}

Trả về: {{"items":[{{"id":1,"text_vi":"..."}}]}}"""
    data = _generate(prompt, schema=FULL_SCHEMA)
    if not isinstance(data, dict):
        raise ValueError("Gemini không trả JSON object")
    out_items = data.get("items")
    if not isinstance(out_items, list):
        raise ValueError("Gemini không trả danh sách items")
    result: dict[int, str] = {}
    for it in out_items:
        try:
            result[int(it["id"])] = str(it["text_vi"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _translate_batch(
    batch: list[Cue],
    entity_dict: dict[str, str],
) -> tuple[list[dict], dict[str, str]]:
    """Dịch 1 batch — tự tách đôi đệ quy nếu JSON lỗi (batch nhỏ, <20 câu thì
    thôi không tách nữa, để lỗi thật lộ ra thay vì tách vô hạn)."""
    try:
        return _one_shot(batch, entity_dict)
    except (json.JSONDecodeError, ValueError) as err:
        if len(batch) < 20:
            raise
        logger.warning("Batch {} câu JSON lỗi ({}), tách 2 nửa", len(batch), err)
        mid = len(batch) // 2
        a_items, a_ent = _translate_batch(batch[:mid], entity_dict)
        time.sleep(2)
        b_items, b_ent = _translate_batch(batch[mid:], {**entity_dict, **a_ent})
        return a_items + b_items, {**a_ent, **b_ent}


def process_llm(
    cues: list[Cue],
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    known_entity_dict: dict[str, str] | None = None,
) -> tuple[list[Cue], dict[str, str]]:
    total = len(cues)
    entity_dict = dict(known_entity_dict or {})
    translated_map: dict[int, str] = {}
    batches = _chunks(cues, BATCH_SIZE)

    for bi, batch in enumerate(batches):
        if on_progress:
            on_progress(bi * BATCH_SIZE, total, f"dịch nhóm {bi + 1}/{len(batches)} · {len(batch)} câu")
        items, new_ent = _translate_batch(batch, entity_dict)
        for item in items:
            try:
                translated_map[int(item["id"])] = str(item["text_vi"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
        entity_dict.update(new_ent)

        # Gemini đôi khi trả JSON hợp lệ về cú pháp nhưng THIẾU vài id (thường
        # gần cuối batch) — không phải lỗi parse nên nhánh tách-đôi ở
        # _translate_batch không bắt được, và trước đây bị âm thầm bỏ qua
        # (giữ nguyên chữ Hán chưa dịch). Tự phát hiện + dịch lại riêng đúng
        # các id bị thiếu trước khi sang batch kế.
        missing = [c for c in batch if c.id not in translated_map]
        if missing:
            logger.warning(
                "Batch {}/{}: thiếu {} câu trong JSON trả về (id {}), dịch lại riêng",
                bi + 1, len(batches), len(missing), [c.id for c in missing],
            )
            try:
                retry_items, retry_ent = _one_shot(missing, entity_dict)
                for item in retry_items:
                    try:
                        translated_map[int(item["id"])] = str(item["text_vi"]).strip()
                    except (KeyError, TypeError, ValueError):
                        continue
                entity_dict.update(retry_ent)
            except Exception as err:
                logger.warning("Dịch lại {} câu thiếu lỗi ({}), giữ nguyên chữ gốc", len(missing), err)
            still_missing = [c.id for c in missing if c.id not in translated_map]
            if still_missing:
                logger.warning("Batch {}/{}: vẫn thiếu {} câu sau khi dịch lại (id {}), giữ nguyên chữ gốc", bi + 1, len(batches), len(still_missing), still_missing)

        if bi < len(batches) - 1:
            time.sleep(BATCH_SLEEP_S)

    logger.info("Gemini: {} câu dịch ({} nhóm), {} tên riêng", len(translated_map), len(batches), len(entity_dict))

    out = [
        Cue(id=c.id, start=c.start, end=c.end, text=translated_map.get(c.id, c.text))
        for c in cues
    ]

    # Lượt 2 — chỉ nén lại đúng những câu vượt MAX_CPS, không đụng câu khác.
    # Cũng chia batch: video dài có thể có hàng trăm câu vượt CPS cùng lúc,
    # nén hết trong 1 lần gọi sẽ vỡ giới hạn output y như lượt dịch chính.
    violators = [(c, c.text) for c in out if c.text.strip() and _cps(c.text, _duration_s(c)) > MAX_CPS]
    if violators:
        if on_progress:
            on_progress(total, total, f"nén {len(violators)} câu đọc gấp")
        rewritten: dict[int, str] = {}
        violator_batches = _chunks(violators, BATCH_SIZE)
        for bi, batch in enumerate(violator_batches):
            try:
                rewritten.update(_compact_rewrite_batch(batch, entity_dict))
            except Exception as err:
                logger.warning("Nén câu đọc gấp lỗi ({}), giữ bản dịch lượt 1 cho nhóm {}/{}", err, bi + 1, len(violator_batches))
            if bi < len(violator_batches) - 1:
                time.sleep(BATCH_SLEEP_S)
        if rewritten:
            for cue in out:
                if cue.id in rewritten:
                    cue.text = rewritten[cue.id]
            logger.info("Lượt 2: nén được {}/{} câu vượt {} CPS", len(rewritten), len(violators), MAX_CPS)

    if on_progress:
        on_progress(len(cues), len(cues), "xong")
    return out, entity_dict


SINGLE_SCHEMA = {
    "type": "object",
    "properties": {"text_vi": {"type": "string"}},
    "required": ["text_vi"],
}


def _load_entity_dict(dict_path: Path) -> dict[str, str]:
    if not dict_path.exists():
        return {}
    raw = json.loads(dict_path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def retranslate_cue(episode_root: Path, cue_id: int, entity_dict_path: Path | None = None) -> str:
    """Dịch lại đúng 1 câu — dùng lại entity_dict đã có, chỉ ghi đè câu đó
    trong sub_vi.srt, không đụng các câu khác (đỡ tốn quota Gemini).

    `entity_dict_path` mặc định = `episode_root/entity_dict.json` (dự án
    đơn); với dự án dài tập truyền entity_dict.json ở cấp project để dùng
    chung tên riêng đã chốt từ mọi tập."""
    zh_cues = {c.id: c for c in load_srt(episode_root / "sub_zh.srt")}
    cue = zh_cues.get(cue_id)
    if cue is None:
        raise ValueError(f"Không tìm thấy câu #{cue_id} trong sub_zh.srt")

    entity_dict = _load_entity_dict(entity_dict_path or (episode_root / "entity_dict.json"))
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

    # Nếu bản dịch tự nhiên vẫn đọc quá gấp, nén lại đúng câu này (không ép
    # súc tích ngay từ đầu — xem lý do ở comment TARGET_CPS/MAX_CPS phía trên).
    if text_vi and _cps(text_vi, _duration_s(cue)) > MAX_CPS:
        try:
            rewritten = _compact_rewrite_batch([(cue, text_vi)], entity_dict)
            if cue_id in rewritten:
                text_vi = rewritten[cue_id]
        except Exception as err:
            logger.warning("Nén câu #{} lỗi ({}), giữ bản dịch tự nhiên", cue_id, err)

    vi_path = project_root / "sub_vi.srt"
    if load_srt(vi_path):
        update_cue_text(vi_path, cue_id, text_vi)
    return text_vi


def translate_project(
    episode_root: Path,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    entity_dict_path: Path | None = None,
) -> tuple[Path, Path, dict[str, str]]:
    """`entity_dict_path` mặc định = `episode_root/entity_dict.json` (dự án
    đơn, hành vi y hệt trước đây). Với dự án dài tập, truyền entity_dict.json
    ở cấp project: tên riêng đã chốt ở các tập trước được nạp vào prompt rồi
    MERGE (không ghi đè) tên riêng mới phát hiện ở tập này vào lại đúng file
    đó — nhất quán xuyên suốt series."""
    zh_path = episode_root / "sub_zh.srt"
    cues = load_srt(zh_path)
    if not cues:
        raise ValueError("Chưa có sub_zh.srt — chạy Whisper trước.")

    dict_path = entity_dict_path or (episode_root / "entity_dict.json")
    known_entity_dict = _load_entity_dict(dict_path)

    vi_cues, new_entity_dict = process_llm(cues, on_progress=on_progress, known_entity_dict=known_entity_dict)
    vi_path = episode_root / "sub_vi.srt"
    write_srt(vi_path, vi_cues)
    merged_entity_dict = {**known_entity_dict, **new_entity_dict}
    dict_path.parent.mkdir(parents=True, exist_ok=True)
    dict_path.write_text(json.dumps(merged_entity_dict, indent=2, ensure_ascii=False), encoding="utf-8")
    return vi_path, dict_path, merged_entity_dict
