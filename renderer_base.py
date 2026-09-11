# -*- coding: utf-8 -*-
import os
import re
import tempfile

from PIL import Image, ImageDraw, ImageFont

def _hex(color: str):
    color = (color or "").lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    try:
        return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (0, 0, 0)


def _blend(c1, c2, t: float):
    """c1 向 c2 混合 t（0~1）"""
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


class RendererBase:
    """一个不依赖浏览器的 Markdown -> 图片排版渲染器。"""

    NO_LINE_START = set("，。！？；：、）》】」』”’…—·％～‰")
    NO_LINE_END = set("（《【「『“‘〈")

    def __init__(self, font_path: str, width: int = 880, font_size: int = 30,
                 accent: str = "#2B5CE6", max_chars: int = 6000,
                 landscape_w: int = 1600, landscape_ratio: float = 1.6,
                 emoji_path: str | None = None):
        self.font_path = font_path
        self.width = int(width)
        self.size = int(font_size)
        self.max_chars = int(max_chars)
        self._accent_hex = accent
        self.landscape_w = int(landscape_w)
        self.landscape_ratio = float(landscape_ratio)
        self._emoji_path = emoji_path
        self._emoji_font = None
        self._emoji_tried = False
        self._emoji_cache = {}
        self._wcache = {}
        self._images = {}

        self.accent = _hex(accent)
        self.bg = _hex("#F2F4F7")
        self.card = _hex("#FFFFFF")
        self.card_line = _hex("#E6E9EF")
        self.ink = _hex("#24292F")
        self.sub = _hex("#667085")
        self.hair = _hex("#E5E8EE")
        self.inline_code_bg = _blend(self.accent, (255, 255, 255), 0.92)
        self.inline_code_fg = self.accent
        self.code_bg = _hex("#262B36")
        self.code_fg = _hex("#E6E9F0")
        self.quote_bar = _blend(self.accent, (255, 255, 255), 0.45)
        self.table_head_bg = _blend(self.accent, (255, 255, 255), 0.90)
        self.zebra = _hex("#F6F8FB")
        self.table_line = _hex("#CBD3DE")

        self.card_margin = 26
        self.card_pad = 46
        self.radius = 28
        self.content_w = self.width - 2 * (self.card_margin + self.card_pad)
        self._fonts = {}
        self._var_supported = None

    def _font(self, size: int, weight: int = 400):
        key = (round(size), weight)
        if key in self._fonts:
            return self._fonts[key]
        f = ImageFont.truetype(self.font_path, round(size))
        var_ok = False
        try:
            f.set_variation_by_axes([weight])
            var_ok = True
        except Exception:
            var_ok = False
        if self._var_supported is None:
            self._var_supported = var_ok
        pair = (f, (weight >= 600 and not var_ok))
        self._fonts[key] = pair
        return pair

    _EMOJI_RE = re.compile(
        "[\U0001F1E6-\U0001F1FF]{2}"
        "|[0-9#*]\ufe0f?\u20e3"
        "|[\u2600-\u27BF\u2B00-\u2BFF\U0001F000-\U0001FAFF]"
        "(?:[\ufe0f\U0001F3FB-\U0001F3FF]|\u200d[\u2600-\u27BF\u2B00-\u2BFF\U0001F000-\U0001FAFF])*"
    )

    def _get_emoji_font(self):
        if not self._emoji_tried:
            self._emoji_tried = True
            if self._emoji_path:
                for sz in (109, 136, 128):
                    try:
                        self._emoji_font = ImageFont.truetype(self._emoji_path, sz)
                        break
                    except Exception:
                        continue
        return self._emoji_font

    def _inline_image(self, st, size):
        im = self._images.get(st.get("img"))
        if im is None:
            return None
        th = max(1, round(size * 1.3))
        scale = th / im.size[1]
        w = max(1, round(im.size[0] * scale))
        if w > self.content_w * 0.5:
            w = round(self.content_w * 0.5)
            th = max(1, round(im.size[1] * (w / im.size[0])))
        return im.resize((w, th), Image.LANCZOS)

    def _emoji_image(self, text: str, size: int):
        key = (text, round(size))
        if key in self._emoji_cache:
            return self._emoji_cache[key]
        layer = None
        ef = self._get_emoji_font()
        if ef is not None:
            try:
                nw = max(1, round(ef.getlength(text)))
                big = Image.new("RGBA", (nw + 48, 180), (0, 0, 0, 0))
                ImageDraw.Draw(big).text((24, 20), text, font=ef, embedded_color=True)
                bbox = big.getbbox()
                if bbox:
                    big = big.crop(bbox)
                    th = max(1, round(size * 1.15))
                    scale = th / big.size[1]
                    layer = big.resize((max(1, round(big.size[0] * scale)), th), Image.LANCZOS)
            except Exception:
                layer = None
        self._emoji_cache[key] = layer
        return layer

    _TABLE_SEP_RE = re.compile(r"^\|?\s*:?-[\s:|-]*$")

    @staticmethod
    def _split_row(line: str):
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    _INLINE_RE = re.compile(
        r"(?P<img>!\[[^\]\n]*\]\([^)\n]+?\))"
        r"|(?P<bold>\*\*[^*\n]+?\*\*|__[^_\n]+?__)"
        r"|(?P<code>`[^`\n]+?`)"
        r"|(?P<link>\[[^\]\n]+?\]\([^)\n]*?\))"
        r"|(?P<strike>~~[^~\n]+?~~)"
        r"|(?P<italic>(?<![A-Za-z0-9_*])\*[^*\n]+?\*(?![A-Za-z0-9_*])|(?<![A-Za-z0-9_])_[^_\n]+?_(?![A-Za-z0-9_]))"
    )

    def _parse_inline(self, text: str):
        segs, pos = [], 0
        for m in self._INLINE_RE.finditer(text):
            if m.start() > pos:
                segs.append((text[pos:m.start()], {}))
            kind, s = m.lastgroup, m.group()
            if kind == "img":
                mm = re.match(r"!\[([^\]]*)\]\(([^)]*)\)", s)
                url = mm.group(2).strip() if mm else ""
                alt = mm.group(1) if mm else ""
                segs.append((alt or "图片", {"img": url, "alt": alt}))
            elif kind == "bold":
                segs.append((s[2:-2], {"bold": True}))
            elif kind == "italic":
                segs.append((s[1:-1], {"italic": True}))
            elif kind == "code":
                segs.append((s[1:-1], {"code": True}))
            elif kind == "strike":
                segs.append((s[2:-2], {"strike": True}))
            elif kind == "link":
                mm = re.match(r"\[([^\]]*)\]\([^)]*\)", s)
                segs.append((mm.group(1) if mm else s, {"link": True}))
            pos = m.end()
        if pos < len(text):
            segs.append((text[pos:], {}))
        return [(t, st) for t, st in segs if t]

    @staticmethod
    def _is_cjk(ch: str) -> bool:
        return ord(ch) >= 0x2E80

    def _parse_blocks(self, md: str):
        lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        blocks, i, para = [], 0, []

        def flush_para():
            if para:
                merged = ""
                for ln in para:
                    if not merged:
                        merged = ln
                    elif self._is_cjk(merged[-1]) or self._is_cjk(ln[0]):
                        merged += ln
                    else:
                        merged += " " + ln
                blocks.append({"type": "p", "segments": self._parse_inline(merged.strip())})
                para.clear()

        while i < len(lines):
            line = lines[i]
            raw = line.rstrip()
            stripped = raw.strip()
            if stripped.startswith("```"):
                flush_para()
                code_lines, i = [], i + 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i].rstrip("\n"))
                    i += 1
                i += 1
                blocks.append({"type": "code", "lines": code_lines})
                continue
            if not stripped:
                flush_para(); i += 1; continue
            m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if m:
                flush_para()
                level = min(len(m.group(1)), 3)
                blocks.append({"type": f"h{level}", "segments": self._parse_inline(m.group(2).strip())})
                i += 1; continue
            if "|" in stripped and i + 1 < len(lines):
                sep = lines[i + 1].strip()
                if "-" in sep and self._TABLE_SEP_RE.match(sep):
                    flush_para()
                    header = self._split_row(stripped)
                    aligns = []
                    for cell in self._split_row(sep):
                        c = cell.strip()
                        aligns.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "left")
                    i += 2
                    rows = []
                    while i < len(lines) and "|" in lines[i] and lines[i].strip():
                        rows.append(self._split_row(lines[i])); i += 1
                    blocks.append({"type": "table", "header": header, "aligns": aligns, "rows": rows})
                    continue
            m_img = re.match(r"^!\[([^\]\n]*)\]\(((?:https?://|md2img://)[^)\s]+)\)$", stripped)
            m_bare = re.match(r"^(https?://\S+?\.(?:png|jpe?g|gif|webp|bmp)(?:\?\S*)?)$", stripped, re.I)
            if m_img or m_bare:
                flush_para()
                alt = m_img.group(1) if m_img else ""
                url = m_img.group(2) if m_img else m_bare.group(1)
                if blocks and blocks[-1]["type"] == "imgblock":
                    blocks[-1]["items"].append((alt, url))
                else:
                    blocks.append({"type": "imgblock", "items": [(alt, url)]})
                i += 1; continue
            if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
                flush_para(); blocks.append({"type": "hr"}); i += 1; continue
            m = re.match(r"^>\s?(.*)$", stripped)
            if m:
                flush_para(); blocks.append({"type": "quote", "segments": self._parse_inline(m.group(1).strip())}); i += 1; continue
            m = re.match(r"^(\s*)([-*+]|\d+[.、)])\s+(.*)$", raw)
            if m:
                flush_para()
                indent = len(m.group(1).replace("\t", "    "))
                marker = m.group(2)
                blocks.append({"type": "li", "depth": min(indent // 2, 3), "ordered": bool(re.match(r"\d", marker)), "marker": marker if re.match(r"\d", marker) else "", "segments": self._parse_inline(m.group(3).strip())})
                i += 1; continue
            para.append(stripped); i += 1
        flush_para()
        return blocks or [{"type": "p", "segments": [("（空内容）", {})]}]

    def _style_font(self, st, size, force_bold=False):
        weight = 500 if st.get("code") else (700 if (st.get("bold") or force_bold) else 400)
        return self._font(size, weight)

    def _wrap(self, segments, size: int, max_width: int, force_bold=False):
        flat = []
        def push_plain(txt, st):
            fp = self._style_font(st, size, force_bold)
            f = fp[0]; cache = self._wcache; fid = id(f)
            for ch in txt:
                key = (fid, ch); w = cache.get(key)
                if w is None:
                    w = f.getlength(ch); cache[key] = w
                flat.append([ch, st, fp, w])
        for text, st in segments:
            if st.get("img"):
                layer = self._inline_image(st, size)
                if layer is not None:
                    flat.append([st.get("alt") or "图片", {**st}, ("IMG", layer), float(layer.size[0])])
                else:
                    alt = st.get("alt") or ""; push_plain("[图片" + ((":" + alt) if alt else "") + "]", {})
                continue
            pos = 0
            for m in self._EMOJI_RE.finditer(text):
                if m.start() > pos: push_plain(text[pos:m.start()], st)
                em = m.group(); layer = self._emoji_image(em, size)
                if layer is not None:
                    flat.append([em, {**st, "emoji": True}, ("EMOJI", layer), float(layer.size[0])])
                else: push_plain(em, st)
                pos = m.end()
            if pos < len(text): push_plain(text[pos:], st)
        lines, cur, cur_w, last_break = [], [], 0.0, -1
        for ch, st, fp, w in flat:
            if cur and cur_w + w > max_width:
                if ch in self.NO_LINE_START and w < max_width * 0.2:
                    cur.append([ch, st, fp, w]); cur_w += w; continue
                if 0 < last_break < len(cur) and (len(cur) - last_break) <= 18:
                    new_cur = cur[last_break:]; cur = cur[:last_break]; lines.append(cur)
                    while new_cur and new_cur[0][0] == " ": new_cur.pop(0)
                    cur = new_cur; cur_w = sum(x[3] for x in cur)
                else:
                    lines.append(cur); cur, cur_w = [], 0.0
                    if len(lines[-1]) > 1 and lines[-1][-1][0] in self.NO_LINE_END:
                        mv = lines[-1].pop(); cur.insert(0, mv); cur_w += mv[3]
                last_break = -1
                for idx, item in enumerate(cur):
                    if item[0] == " ": last_break = idx + 1
            cur.append([ch, st, fp, w]); cur_w += w
            if ch == " ": last_break = len(cur)
        if cur: lines.append(cur)
        merged = []
        for line in lines:
            runs = []
            for ch, st, fp, w in line:
                if runs and not st.get("emoji") and runs[-1][1] == st and runs[-1][2] is fp:
                    runs[-1][0] += ch; runs[-1][3] += w
                else: runs.append([ch, st, fp, w])
            merged.append([(t, st, fp, w) for t, st, fp, w in runs])
        return merged
