# -*- coding: utf-8 -*-
import os
import tempfile

from PIL import Image, ImageDraw

try:
    from .renderer_base import RendererBase
    from .renderer_layout import RendererLayoutMixin
    from .renderer_table import RendererTableMixin
    from .renderer_draw import RendererDrawMixin
except ImportError:
    from renderer_base import RendererBase
    from renderer_layout import RendererLayoutMixin
    from renderer_table import RendererTableMixin
    from renderer_draw import RendererDrawMixin


class MarkdownRenderer(RendererDrawMixin, RendererTableMixin, RendererLayoutMixin, RendererBase):
    def render_blocks(self, blocks) -> Image.Image:
        ops, total_h = self._layout(blocks)
        img = Image.new("RGB", (self.width, total_h), self.bg)
        d = ImageDraw.Draw(img)
        m = self.card_margin
        d.rounded_rectangle([m, m, self.width - m, total_h - m],
                            radius=self.radius, fill=self.card,
                            outline=self.card_line, width=2)
        for op in ops:
            kind = op[0]
            if kind == "line":
                _, x, y, runs, line_h, color = op
                self._draw_line(d, img, x, y, runs, line_h, color)
            elif kind == "rect":
                _, x1, y1, x2, y2, r, fill = op
                d.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=fill)
            elif kind == "text":
                _, x, y, text, fp, color = op
                d.text((x, y), text, font=fp[0], fill=color,
                       stroke_width=1 if fp[1] else 0, stroke_fill=color)
            elif kind == "diamond":
                _, cx, cy, r, fill = op
                d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=fill)
            elif kind == "table":
                self._draw_table(d, img, op[1])
            elif kind == "photo":
                _, px, py, pw, ph, url = op
                pim = self._images.get(url)
                if pim is not None:
                    pim2 = pim.convert("RGBA").resize((pw, ph), Image.LANCZOS)
                    mask = Image.new("L", (pw, ph), 0)
                    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw, ph], radius=14, fill=255)
                    img.paste(pim2, (px, py), mask)
                    d.rounded_rectangle([px, py, px + pw, py + ph],
                                        radius=14, outline=self.card_line, width=2)
        return img

    def render(self, md_text: str, images: dict | None = None) -> Image.Image:
        self._images = images or {}
        md_text = (md_text or "").strip()
        if len(md_text) > self.max_chars:
            md_text = md_text[: self.max_chars] + "\n\n> ……（内容过长，已截断）"
        return self.render_blocks(self._parse_blocks(md_text))

    def render_split(self, md_text: str, images: dict | None = None):
        self._images = images or {}
        md_text = (md_text or "").strip()
        if len(md_text) > self.max_chars:
            md_text = md_text[: self.max_chars] + "\n\n> ……（内容过长，已截断）"
        blocks = self._parse_blocks(md_text)
        main_blocks, land_tables = [], []
        for b in blocks:
            if b["type"] == "table" and self.table_needs_landscape(b):
                land_tables.append(b)
                main_blocks.append({"type": "note"})
            else:
                main_blocks.append(b)
        images_out = [self.render_blocks(main_blocks)]
        for tb in land_tables:
            lr = MarkdownRenderer(self.font_path, width=self.landscape_w,
                                  font_size=self.size + 2, accent=self._accent_hex,
                                  max_chars=self.max_chars,
                                  landscape_w=self.landscape_w,
                                  landscape_ratio=self.landscape_ratio,
                                  emoji_path=self._emoji_path)
            lr._fonts = self._fonts
            lr._wcache = self._wcache
            lr._emoji_cache = self._emoji_cache
            lr._emoji_font = self._emoji_font
            lr._emoji_tried = self._emoji_tried
            lr._var_supported = self._var_supported
            lr._images = self._images
            images_out.append(lr.render_blocks([tb]))
        return images_out

    def render_to_files(self, md_text: str, images: dict | None = None):
        paths = []
        try:
            for img in self.render_split(md_text, images):
                fd, path = tempfile.mkstemp(prefix="md2img_", suffix=".png")
                os.close(fd)
                img.save(path, "PNG", compress_level=2)
                paths.append(path)
            return paths
        except Exception:
            for path in paths:
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError:
                    pass
            raise

    def render_to_file(self, md_text: str) -> str:
        return self.render_to_files(md_text)[0]
