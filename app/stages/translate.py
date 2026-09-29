from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from .. import config
from ..jobs import JobCancelled, current_job
from ..utils.srt import Cue, load_srt, parse_ts, update_cue_text, write_srt

# Tốc độ đọc tự nhiên mục tiêu / ngưỡng chấp nhận được (ký tự việt/giây) —
# đọc động từ config (đổi được qua Settings, xem config.TRANSLATE_PACE_PROFILES)
# thay vì hardcode, vì đây là đánh đổi tuỳ video (thoại càng dồn dập càng cần
# nới lỏng để giữ đủ ý thay vì cắt bớt).
# Đã thử nhét ngân sách thời lượng vào prompt dịch 1-lần-cho-cả-315-câu — cải
# thiện CPS thật nhưng làm Gemini áp 1 văn phong súc tích cho TOÀN BỘ phản
# hồi (kể cả câu không hề gấp), gây mất tên riêng/từ nối, đọc cộc lốc. Nên
# tách 2 lượt: dịch tự nhiên bình thường trước (không nhắc ngân sách gì cả),
# rồi CHỈ gom đúng những câu thật sự vượt MAX_CPS gửi riêng 1 lượt nén lại —
# giữ chất lượng tự nhiên cho phần lớn câu còn lại.


def _target_cps() -> float:
    return config.translate_cps()[0]


def _max_cps() -> float:
    return config.translate_cps()[1]

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


def _retry_after_s(err: BaseException, attempt: int) -> Optional[float]:
    if _is_daily_free_tier(err):
        return None
    m = re.search(r"retry in ([\d.]+)\s*s", _err_text(err), re.I)
    if m:
        return min(float(m.group(1)) + 1.0, 120.0)
    s = _err_text(err).lower()
    if "429" in s or "resource_exhausted" in s:
        return 30.0
    if any(tok in s for tok in ("504", "deadline_exceeded", "deadline expired", "timed out", "timeout")):
        # Gọi bị treo rồi hết giờ (504 phía Google hoặc GEMINI_CALL_TIMEOUT_S
        # phía mình) — thường gọi lại là qua ngay (đo thật: lần sau 1s).
        return 3.0
    if any(tok in s for tok in ("503", "unavailable", "high demand", "try again")):
        # "503 UNAVAILABLE — model đang quá tải" — LỖI TẠM THỜI PHÍA GOOGLE,
        # không phải lỗi code/quota — với job nền tự động (không ai đứng chờ
        # trước màn hình) đáng kiên nhẫn chờ lâu hơn hẳn mức cũ (8s x 4 lần =
        # 32s tổng, thường không đủ khi Google quá tải kéo dài) hơn là bỏ
        # cuộc rồi bắt cả video tải+transcribe lại từ đầu (tốn hơn hẳn việc
        # chờ thêm vài phút). Backoff tăng dần theo lần thử: 8s, 16s, 24s...
        # tối đa 60s/lần.
        return min(8.0 * (attempt + 1), 60.0)
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


def _sleep_cancellable(seconds: float) -> None:
    job = current_job()
    if job is None:
        time.sleep(seconds)
        return
    if job.cancel_event.wait(seconds):
        job.raise_if_cancelled()


# Giới hạn thời gian 1 lần gọi Gemini. Trước đây không có: đã đo thật lúc
# Google chập chờn (gemini-flash-lite-latest) — 2/3 lần gọi treo tới khi tự
# trả 504 DEADLINE_EXCEEDED, job dịch 12 câu kẹt hơn 10 phút. 120s dư cho 1
# batch 200 câu bình thường (vài chục giây), quá mức là coi như treo, thử lại.
GEMINI_CALL_TIMEOUT_S = 120


def _generate(prompt: str, schema: dict | None = None) -> Any:
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=_api_key(),
        http_options=types.HttpOptions(timeout=GEMINI_CALL_TIMEOUT_S * 1000),
    )
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

    def call_blocking(with_thinking: bool):
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

    def call(with_thinking: bool):
        # Chạy lời gọi trong thread phụ, còn thread job chờ có kiểm tra cờ Dừng
        # mỗi 0.5s — trước đây bấm Dừng phải chờ Google trả lời xong (Google
        # treo thì hàng phút) mới dừng được. Dừng giữa chừng thì bỏ mặc thread
        # phụ tự kết thúc (tối đa GEMINI_CALL_TIMEOUT_S), kết quả bị bỏ.
        job = current_job()
        if job is None:
            return call_blocking(with_thinking)
        job.raise_if_cancelled()
        box: dict = {}

        def worker() -> None:
            try:
                box["result"] = call_blocking(with_thinking)
            except BaseException as err:  # chuyển lỗi về thread job
                box["error"] = err

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while t.is_alive():
            t.join(0.5)
            job.raise_if_cancelled()
        if "error" in box:
            raise box["error"]
        return box["result"]

    last: BaseException | None = None
    thinking = True
    # 8 lần thử (trước đây 4) — job nền tự động không ai đứng chờ, đáng kiên
    # nhẫn hơn với lỗi tạm thời phía Google (503 UNAVAILABLE) thay vì bỏ
    # cuộc sớm rồi bắt cả video tải+transcribe lại từ đầu (xem `_retry_after_s`).
    _MAX_ATTEMPTS = 8
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return call(with_thinking=thinking)
        except JobCancelled:
            raise
        except Exception as err:
            last = err
            if _is_daily_free_tier(err):
                raise GeminiQuotaError(_quota_message(err)) from err
            msg = _err_text(err).lower()
            if thinking and model.startswith("gemini-3") and "thinking" in msg:
                thinking = False
                logger.warning("Bỏ thinking_config rồi gọi lại")
                continue
            wait = _retry_after_s(err, attempt)
            if wait is None or attempt == _MAX_ATTEMPTS - 1:
                raise
            logger.warning("Gemini retry sau {:.0f}s (lần {}/{}) ({})", wait, attempt + 1, _MAX_ATTEMPTS, err)
            _sleep_cancellable(wait)
    assert last is not None
    raise last


_TITLE_SCHEMA = {
    "type": "object",
    "properties": {"vi": {"type": "string"}},
    "required": ["vi"],
}


def translate_title(title: str) -> str:
    """Dịch nhanh 1 tiêu đề ngắn (vd title video Douyin) sang tiếng Việt —
    dùng cho "Dự án tự động" (caption đăng TikTok + tên dự án hiển thị), tách
    riêng khỏi `process_llm`/`_one_shot` vì đó là dịch NGUYÊN 1 danh sách cue
    dài theo batch, không hợp cho 1 câu ngắn lẻ. Trả nguyên `title` nếu rỗng
    hoặc Gemini lỗi (không chặn luồng crawl/kích hoạt vì lỗi dịch tiêu đề)."""
    title = (title or "").strip()
    if not title:
        return title
    prompt = (
        "Dịch tiêu đề video ngắn sau đây sang tiếng Việt tự nhiên, giữ nguyên "
        "văn phong ngắn gọn kiểu tiêu đề mạng xã hội, không thêm giải thích:\n\n"
        f"{title}"
    )
    try:
        result = _generate(prompt, schema=_TITLE_SCHEMA)
        vi = str(result.get("vi") or "").strip()
        return vi or title
    except JobCancelled:
        raise
    except Exception as err:
        logger.warning("translate_title: dịch tiêu đề thất bại, giữ nguyên bản gốc: {}", err)
        return title


# ---- Caption đăng TikTok/Facebook cho "Dự án tự động"
# Trước đây caption = dịch nguyên văn tiêu đề Douyin: giữ cả hashtag đặc thù
# Douyin (#萌宠出道计划, #神奇动物在抖音, @抖音小助手...) dịch/phiên âm thành
# hashtag vô nghĩa, có dấu, dài lê thê (#hiếuthảophảisớmkhôngnênđểlạitiếcnuối),
# không kể nội dung video, không có câu kéo tương tác. Giờ viết caption MỚI theo
# mẫu người dùng đưa: 1 dòng tiêu đề thu hút + emoji, 2-3 đoạn ngắn kể nội dung,
# 1 câu hỏi cuối, rồi 5-8 hashtag ngắn viết liền không dấu (HoaChuCaiDau).

_CAPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "paragraphs": {"type": "array", "items": {"type": "string"}},
        "question": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook", "paragraphs", "question", "hashtags"],
}

# Rác đi kèm khi copy link chia sẻ Douyin: link, lời nhắc "复制此链接...",
# chuỗi mã/giờ đầu đoạn share ("1.56 :7pm t@E.UY Fho:/ 03/01").
_DOUYIN_JUNK_RE = [
    re.compile(r"https?://\S+"),
    re.compile(r"复制此链接.*$", re.S),
    re.compile(r"打开\s*Dou音.*$", re.S),
    re.compile(r"@\S+"),
    re.compile(r"^[\x00-\x7F]*?/\s*\d{1,2}/\d{1,2}\s+"),
]
# Hashtag chỉ có nghĩa trên Douyin (chiến dịch/tính năng của Douyin) — bỏ.
_DOUYIN_ONLY_TAG_WORDS = ("抖音", "dou", "douyin", "上热门", "计划", "小助手", "创作者", "tiktokvn")
MAX_CAPTION_HASHTAGS = 8


def clean_douyin_title(title: str) -> str:
    text = (title or "").strip()
    for pattern in _DOUYIN_JUNK_RE:
        text = pattern.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_hashtag(tag: str) -> str:
    """"#mực vây lớn" / "Mực Vây Lớn" → "MucVayLon": bỏ dấu, viết liền, hoa chữ
    cái đầu mỗi từ; giữ nguyên chữ hoa sẵn có (vd "AIContent")."""
    import unicodedata

    raw = tag.strip().lstrip("#").replace("đ", "d").replace("Đ", "D")
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    words = re.findall(r"[A-Za-z0-9]+", raw)
    return "".join(w[:1].upper() + w[1:] for w in words)


def _clean_hashtags(tags: list[str], fixed: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for tag in [*tags, *fixed]:
        low = tag.lower()
        if any(w in low for w in _DOUYIN_ONLY_TAG_WORDS):
            continue
        norm = _normalize_hashtag(tag)
        if not (2 <= len(norm) <= 24) or norm.lower() in seen:
            continue
        seen.add(norm.lower())
        out.append(norm)
    # Hashtag cố định của dự án luôn được giữ, kể cả khi vượt giới hạn số lượng.
    fixed_norm = {_normalize_hashtag(t).lower() for t in fixed}
    head = [t for t in out if t.lower() not in fixed_norm][: max(0, MAX_CAPTION_HASHTAGS - len(fixed_norm))]
    return head + [t for t in out if t.lower() in fixed_norm]


def generate_caption(
    title: str,
    content_vi: str = "",
    entity_dict: dict[str, str] | None = None,
    fixed_hashtags: list[str] | None = None,
) -> str:
    """Viết caption đăng mạng xã hội (TikTok/Facebook) cho 1 video reup.
    `content_vi` = lời thoại tiếng Việt của video (phụ đề đã dịch) để caption
    kể đúng nội dung; `entity_dict` để tên riêng khớp lời thuyết minh;
    `fixed_hashtags` = hashtag cố định của dự án (luôn có). Gemini lỗi thì trả
    về tiêu đề đã dịch nhanh (`translate_title`) để không chặn việc đăng."""
    fixed = [t for t in (fixed_hashtags or []) if t.strip()]
    source = clean_douyin_title(title)
    content = (content_vi or "").strip()[:3500]
    names = "\n".join(f"- {k} → {v}" for k, v in list((entity_dict or {}).items())[:40])
    prompt = f"""Bạn là người viết caption cho kênh TikTok/Facebook tiếng Việt, đăng lại video
từ Douyin đã được lồng tiếng Việt. Viết caption MỚI (không dịch từng chữ tiêu đề gốc), bám
đúng nội dung video, văn phong tự nhiên, cuốn hút, phù hợp người xem Việt Nam.

Cấu trúc:
- hook: 1 dòng tiêu đề thu hút (tối đa ~90 ký tự), kết thúc bằng 1-2 emoji hợp chủ đề.
- paragraphs: 2-3 đoạn ngắn (mỗi đoạn 1-2 câu) kể điểm hấp dẫn của video; có thể thêm 1 emoji
  cuối 1 đoạn. Không bịa chi tiết không có trong nội dung.
- question: 1 câu hỏi mở cuối bài để người xem bình luận.
- hashtags: 6-8 hashtag NGẮN (1-3 từ), đúng chủ đề, là từ khoá người Việt hay tìm; tiếng Việt
  hoặc tiếng Anh tuỳ cái nào phổ biến hơn (vd Pokemon, AIContent). KHÔNG dùng hashtag đặc thù
  Douyin (chiến dịch, tên tính năng, @tài khoản). KHÔNG phiên âm pinyin/Hán Việt tên gốc tiếng
  Trung thành hashtag — dùng tên tiếng Việt thông dụng (vd MucVayLon) hoặc tên tiếng Anh.

Tên riêng phải viết ĐÚNG như bảng sau (khớp lời thuyết minh):
{names or "(không có)"}

Tiêu đề gốc (Douyin): {source or "(trống)"}

Lời thoại tiếng Việt của video:
{content or "(không có)"}"""
    try:
        data = _generate(prompt, schema=_CAPTION_SCHEMA)
        hook = str(data.get("hook") or "").strip()
        paragraphs = [str(p).strip() for p in (data.get("paragraphs") or []) if str(p).strip()][:3]
        question = str(data.get("question") or "").strip()
        tags = _clean_hashtags([str(t) for t in (data.get("hashtags") or [])], fixed)
        if not hook:
            raise ValueError("Gemini không trả tiêu đề caption")
        body = "\n\n".join(x for x in [hook, *paragraphs, question] if x)
        return body + ("\n\n" + " ".join(f"#{t}" for t in tags) if tags else "")
    except JobCancelled:
        raise
    except Exception as err:
        logger.warning("generate_caption: viết caption thất bại, dùng tiêu đề dịch nhanh: {}", err)
        fallback = translate_title(source or title)
        tags = _clean_hashtags([], fixed)
        return fallback + ("\n\n" + " ".join(f"#{t}" for t in tags) if tags else "")


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

Làm CẢ 4 việc trong 1 JSON:
1. entity_dict: tên riêng (người/địa danh/công ty/nickname) → phiên âm tiếng Việt nhất quán.
   Không có thì {{}}. Nếu câu nguồn có cụm rõ ràng bị nghe nhầm (lỗi ASR) và nghi là tên riêng —
   giữ dạng phiên âm hợp lý, TUYỆT ĐỐI KHÔNG dịch nghĩa đen thành từ thật không liên quan ngữ cảnh.
2. Clean từng câu: bỏ filler, sửa lỗi nghe nhầm rõ ràng (đặc biệt lỗi đồng âm tiếng Trung). Câu
   nguồn có thể tới từ OCR phụ đề cứng trên khung hình — sửa luôn lỗi đọc nhầm ký tự rõ ràng của
   OCR (vd nét chữ giống nhau bị lẫn, phồn thể/giản thể lẫn lộn) dựa vào ngữ cảnh câu.
3. Loại rác OCR: nếu câu nguồn là rác thật sự — không phải câu thoại/phụ đề có nghĩa (vd chỉ vài
   ký tự vô nghĩa do đọc lệch khung hình, watermark/tên kênh/đường link lẫn vào vùng phụ đề, ký tự
   lặp/nhiễu không tạo thành câu nào) — trả "text_vi" RỖNG ("") cho đúng id đó thay vì cố dịch. CHỈ
   bỏ khi chắc chắn là rác, KHÔNG bỏ câu thoại ngắn nhưng có nghĩa (vd cảm thán "啊", "什么").
4. Dịch đủ MỌI câu còn lại, giữ đúng id, áp dụng entity_dict nhất quán cho MỌI lần tên đó xuất hiện
   — KHÔNG được bỏ tên riêng thay bằng đại từ/mô tả mơ hồ. Giữ giọng điệu gốc (cảm thán, tiếng lóng
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
    target_cps = _target_cps()
    payload = [
        {
            "id": cue.id,
            "zh": cue.text,
            "current_vi": vi_text,
            "available_duration_s": round(_duration_s(cue), 2),
            "max_chars_vi": max(round(_duration_s(cue) * target_cps), 1),
        }
        for cue, vi_text in items
    ]
    prompt = f"""Các câu tiếng Việt dưới đây ("current_vi") đọc TTS sẽ bị GẤP vì dài hơn
"available_duration_s" giây cho phép — cần viết lại NGẮN GỌN hơn, bám sát "max_chars_vi" ký tự
(tốc độ đọc tự nhiên ~{target_cps} ký tự/giây).

Cách làm: bỏ từ đệm/thừa/trạng từ không cần thiết, dùng từ/cụm ngắn hơn nghĩa tương đương, gộp ý
nếu được. ƯU TIÊN GIỮ LẠI nội dung cốt truyện quan trọng (hành động chính, thông tin/số liệu quan
trọng, twist/mâu thuẫn) — nếu bắt buộc phải cắt bớt ý để vừa số ký tự, cắt phần MÔ TẢ/CẢM THÁN/nhấn
mạnh trước, đừng cắt phần cốt truyện. TUYỆT ĐỐI KHÔNG:
- Bỏ tên riêng rồi thay bằng đại từ/mô tả mơ hồ (vd "cậu ấy", "người kia") — giữ nguyên tên.
- Viết cộc lốc thiếu ngữ pháp hay khó hiểu — vẫn phải là câu tiếng Việt tự nhiên, nghe xuôi tai.
- Cắt bỏ chi tiết cốt truyện (ai làm gì, chuyện gì xảy ra) chỉ để đọc nhanh hơn.
Nếu không thể rút ngắn thêm mà vẫn giữ tự nhiên + đủ ý, giữ nguyên current_vi.

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
            except JobCancelled:
                raise
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
    max_cps = _max_cps()
    violators = [(c, c.text) for c in out if c.text.strip() and _cps(c.text, _duration_s(c)) > max_cps]
    if violators:
        if on_progress:
            on_progress(total, total, f"nén {len(violators)} câu đọc gấp")
        rewritten: dict[int, str] = {}
        violator_batches = _chunks(violators, BATCH_SIZE)
        for bi, batch in enumerate(violator_batches):
            try:
                rewritten.update(_compact_rewrite_batch(batch, entity_dict))
            except JobCancelled:
                raise
            except Exception as err:
                logger.warning("Nén câu đọc gấp lỗi ({}), giữ bản dịch lượt 1 cho nhóm {}/{}", err, bi + 1, len(violator_batches))
            if bi < len(violator_batches) - 1:
                time.sleep(BATCH_SLEEP_S)
        if rewritten:
            for cue in out:
                if cue.id in rewritten:
                    cue.text = rewritten[cue.id]
            logger.info("Lượt 2: nén được {}/{} câu vượt {} CPS", len(rewritten), len(violators), max_cps)

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
    if text_vi and _cps(text_vi, _duration_s(cue)) > _max_cps():
        try:
            rewritten = _compact_rewrite_batch([(cue, text_vi)], entity_dict)
            if cue_id in rewritten:
                text_vi = rewritten[cue_id]
        except JobCancelled:
            raise
        except Exception as err:
            logger.warning("Nén câu #{} lỗi ({}), giữ bản dịch tự nhiên", cue_id, err)

    vi_path = episode_root / "sub_vi.srt"
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
