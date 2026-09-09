from __future__ import annotations

"""PROTOTYPE — chưa nối vào pipeline chính (main.py chưa gọi module này).

Gộp các cue tiếng Trung liên tiếp thành "Narration Block" thay vì ép mỗi cue
là 1 đoạn voice riêng — xem thảo luận trong conversation. Bản đầu tiên này
CHỈ xét duration + gap (không xét semantic boundary bằng LLM), dùng để đo thử
hiệu quả trước khi quyết định có đáng đổi cả data model của app hay không.
"""

from dataclasses import dataclass

from ..utils.srt import Cue, parse_ts

# Tỷ lệ nở chữ Trung -> Việt đo được thực tế trên vài project reup (dao động
# ~2.9x-3.6x tùy văn phong/entity) — dùng để ƯỚC LƯỢNG trước khi dịch thật,
# không phải con số chính xác (TTS đo thật mới là ground truth — xem Bước C).
EXPANSION_RATIO = 3.0

TARGET_CPS = 15.5  # ký tự việt/giây — tốc độ đọc tự nhiên mong muốn
MAX_BLOCK_DURATION_S = 14.0  # trần độ dài 1 block — tránh đoạn văn quá dài khó đọc/khó hiểu
MAX_GAP_BORROW_S = 4.0  # 1 block được "mượn" tối đa bao nhiêu giây từ khoảng lặng ngay sau nó


@dataclass
class NarrationBlock:
    id: int
    cue_ids: list[int]
    start_s: float
    end_s: float  # hết câu cuối cùng trong block (CHƯA tính gap mượn)
    available_end_s: float  # đã cộng phần gap mượn — ngân sách TTS thực tế
    zh_text: str

    @property
    def duration_s(self) -> float:
        return max(self.end_s - self.start_s, 0.01)

    @property
    def available_duration_s(self) -> float:
        return max(self.available_end_s - self.start_s, 0.01)

    @property
    def zh_chars(self) -> int:
        return len(self.zh_text.replace(" ", ""))


def _flush(group: list[Cue], next_id: int) -> NarrationBlock:
    start_s = parse_ts(group[0].start)
    end_s = parse_ts(group[-1].end)
    zh_text = " ".join(c.text.strip() for c in group if c.text.strip())
    return NarrationBlock(
        id=next_id,
        cue_ids=[c.id for c in group],
        start_s=start_s,
        end_s=end_s,
        available_end_s=end_s,
        zh_text=zh_text,
    )


def build_blocks(
    cues: list[Cue],
    target_cps: float = TARGET_CPS,
    max_block_duration_s: float = MAX_BLOCK_DURATION_S,
    max_gap_borrow_s: float = MAX_GAP_BORROW_S,
    expansion_ratio: float = EXPANSION_RATIO,
) -> list[NarrationBlock]:
    """Gộp cue liên tiếp bằng thuật toán tham lam: cứ thêm cue kế tiếp vào
    block hiện tại miễn là (a) tổng thời lượng chưa vượt trần và (b) CPS tiếng
    Việt ƯỚC LƯỢNG (theo expansion_ratio) vẫn còn trong ngân sách — hết 1 trong
    2 điều kiện thì chốt block, bắt đầu block mới."""
    cues = [c for c in cues if c.text.strip()]
    if not cues:
        return []

    blocks: list[NarrationBlock] = []
    current: list[Cue] = [cues[0]]

    for cue in cues[1:]:
        candidate = current + [cue]
        start_s = parse_ts(candidate[0].start)
        end_s = parse_ts(candidate[-1].end)
        duration_s = max(end_s - start_s, 0.01)
        zh_chars = sum(len(c.text.replace(" ", "")) for c in candidate)
        estimated_cps = (zh_chars * expansion_ratio) / duration_s

        if duration_s <= max_block_duration_s and estimated_cps <= target_cps:
            current = candidate
        else:
            blocks.append(_flush(current, len(blocks) + 1))
            current = [cue]

    blocks.append(_flush(current, len(blocks) + 1))

    for i, block in enumerate(blocks):
        if i + 1 < len(blocks):
            gap_s = max(blocks[i + 1].start_s - block.end_s, 0.0)
            block.available_end_s = block.end_s + min(gap_s, max_gap_borrow_s)

    return blocks
