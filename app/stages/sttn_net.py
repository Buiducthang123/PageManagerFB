"""STTN — Spatial-Temporal Transformer Network cho vá video (chỉ phần generator dùng để suy luận).

Nguồn: https://github.com/researchmm/STTN (MIT License, Copyright (c) 2020 researchmm),
bản rút gọn dùng trong https://github.com/YaoFANGUK/video-subtitle-remover (Apache-2.0).
Giữ nguyên kiến trúc để nạp đúng trọng số `sttn.pth` (khoá 'netG'); bỏ Discriminator và
phần khởi tạo trọng số dùng lúc huấn luyện. Không import gì từ package `app` — file này được
worker (hardsub_worker.py, chạy trong venv GPU riêng) import như module cùng thư mục.

Đầu vào cố định 432x240 (patchsize bên dưới tính theo feature 108x60 sau encoder /4).
"""
import math

import torch
import torch.nn as nn
import torch.nn.functional as F

INPUT_W, INPUT_H = 432, 240


class InpaintGenerator(nn.Module):
    def __init__(self):
        super().__init__()
        channel = 256
        stack_num = 8
        patchsize = [(108, 60), (36, 20), (18, 10), (9, 5)]
        self.transformer = nn.Sequential(*[TransformerBlock(patchsize, hidden=channel) for _ in range(stack_num)])
        self.encoder = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=3, stride=2, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(64, 64, kernel_size=3, stride=1, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(64, 128, kernel_size=3, stride=2, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(128, channel, kernel_size=3, stride=1, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
        )
        self.decoder = nn.Sequential(
            deconv(channel, 128, kernel_size=3, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(128, 64, kernel_size=3, stride=1, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            deconv(64, 64, kernel_size=3, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(64, 3, kernel_size=3, stride=1, padding=1),
        )

    def infer(self, feat, masks):
        """feat: (t, c, h/4, w/4) đã qua encoder; masks: (t, 1, h, w) 1 = cần vá."""
        masks = F.interpolate(masks, scale_factor=1.0 / 4)
        _, c, _, _ = feat.size()
        return self.transformer({"x": feat, "m": masks, "b": 1, "c": c})["x"]


class deconv(nn.Module):
    def __init__(self, input_channel, output_channel, kernel_size=3, padding=0):
        super().__init__()
        self.conv = nn.Conv2d(input_channel, output_channel, kernel_size=kernel_size, stride=1, padding=padding)

    def forward(self, x):
        x = F.interpolate(x, scale_factor=2, mode="bilinear", align_corners=True)
        return self.conv(x)


class Attention(nn.Module):
    """Scaled dot-product attention."""

    def forward(self, query, key, value, m):
        scores = torch.matmul(query, key.transpose(-2, -1)) / math.sqrt(query.size(-1))
        # Bản gốc có `scores.masked_fill(m, -1e9)` nhưng KHÔNG gán lại kết quả -> không có tác
        # dụng, và trọng số được huấn luyện với đúng hành vi đó. Bỏ dòng ấy (kết quả y hệt);
        # "sửa" thành gán lại sẽ làm lệch kết quả, còn giữ nguyên thì -1e9 tràn số ở fp16.
        p_attn = F.softmax(scores, dim=-1)
        return torch.matmul(p_attn, value), p_attn


class MultiHeadedAttention(nn.Module):
    def __init__(self, patchsize, d_model):
        super().__init__()
        self.patchsize = patchsize
        self.query_embedding = nn.Conv2d(d_model, d_model, kernel_size=1, padding=0)
        self.value_embedding = nn.Conv2d(d_model, d_model, kernel_size=1, padding=0)
        self.key_embedding = nn.Conv2d(d_model, d_model, kernel_size=1, padding=0)
        self.output_linear = nn.Sequential(nn.Conv2d(d_model, d_model, kernel_size=3, padding=1),
                                           nn.LeakyReLU(0.2, inplace=True))
        self.attention = Attention()

    def forward(self, x, m, b, c):
        bt, _, h, w = x.size()
        t = bt // b
        d_k = c // len(self.patchsize)
        output = []
        _query = self.query_embedding(x)
        _key = self.key_embedding(x)
        _value = self.value_embedding(x)
        for (width, height), query, key, value in zip(self.patchsize,
                                                      torch.chunk(_query, len(self.patchsize), dim=1),
                                                      torch.chunk(_key, len(self.patchsize), dim=1),
                                                      torch.chunk(_value, len(self.patchsize), dim=1)):
            out_w, out_h = w // width, h // height
            mm = m.view(b, t, 1, out_h, height, out_w, width)
            mm = mm.permute(0, 1, 3, 5, 2, 4, 6).contiguous().view(b, t * out_h * out_w, height * width)
            mm = (mm.mean(-1) > 0.5).unsqueeze(1).repeat(1, t * out_h * out_w, 1)
            query = query.view(b, t, d_k, out_h, height, out_w, width)
            query = query.permute(0, 1, 3, 5, 2, 4, 6).contiguous().view(b, t * out_h * out_w, d_k * height * width)
            key = key.view(b, t, d_k, out_h, height, out_w, width)
            key = key.permute(0, 1, 3, 5, 2, 4, 6).contiguous().view(b, t * out_h * out_w, d_k * height * width)
            value = value.view(b, t, d_k, out_h, height, out_w, width)
            value = value.permute(0, 1, 3, 5, 2, 4, 6).contiguous().view(b, t * out_h * out_w, d_k * height * width)
            y, _ = self.attention(query, key, value, mm)
            y = y.view(b, t, out_h, out_w, d_k, height, width)
            y = y.permute(0, 1, 4, 2, 5, 3, 6).contiguous().view(bt, d_k, h, w)
            output.append(y)
        return self.output_linear(torch.cat(output, 1))


class FeedForward(nn.Module):
    def __init__(self, d_model):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(d_model, d_model, kernel_size=3, padding=2, dilation=2),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(d_model, d_model, kernel_size=3, padding=1),
            nn.LeakyReLU(0.2, inplace=True))

    def forward(self, x):
        return self.conv(x)


class TransformerBlock(nn.Module):
    def __init__(self, patchsize, hidden=128):
        super().__init__()
        self.attention = MultiHeadedAttention(patchsize, d_model=hidden)
        self.feed_forward = FeedForward(hidden)

    def forward(self, x):
        x, m, b, c = x["x"], x["m"], x["b"], x["c"]
        x = x + self.attention(x, m, b, c)
        x = x + self.feed_forward(x)
        return {"x": x, "m": m, "b": b, "c": c}


def load_generator(path, device):
    net = InpaintGenerator()
    state = torch.load(str(path), map_location="cpu", weights_only=False)["netG"]
    net.load_state_dict(state)
    return net.to(device).eval()
