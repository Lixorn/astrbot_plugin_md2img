# -*- coding: utf-8 -*-
from PIL import Image, ImageDraw

class RendererDrawMixin:
    def _draw_line(self, d: ImageDraw.ImageDraw, img: Image.Image,
                   x, y, runs, line_h, default_color):
        for text, st, fp, w in runs:
            if isinstance(fp, tuple) and fp[0] in ("EMOJI", "IMG"):
                layer = fp[1]
                mask = layer if layer.mode == "RGBA" else None
                img.paste(layer, (round(x), y + (line_h - layer.size[1]) // 2), mask)
                x += w
                continue
            font, fake_bold = fp
            color = default_color
            if st.get("code"):
                d.rounded_rectangle([x - 7, y - 3, x + w + 7, y + line_h - 4],
                                    radius=8, fill=self.inline_code_bg)
                color = self.inline_code_fg
            elif st.get("link"):
                color = self.accent
            sw = 1 if fake_bold else 0
            if st.get("italic"):
                th = line_h
                layer = Image.new("RGBA", (int(w) + th, th), (0, 0, 0, 0))
                ld = ImageDraw.Draw(layer)
                ld.text((0, 0), text, font=font, fill=color + (255,),
                        stroke_width=sw, stroke_fill=color + (255,))
                shear = 0.24
                layer = layer.transform(layer.size, Image.AFFINE,
                                        (1, -shear, th * shear * 0.45, 0, 1, 0),
                                        resample=Image.BICUBIC)
                img.paste(layer, (round(x), y), layer)
            else:
                d.text((x, y), text, font=font, fill=color,
                       stroke_width=sw, stroke_fill=color)
            ascent, _ = font.getmetrics()
            if st.get("link"):
                uy = y + ascent + 3
                d.line([x, uy, x + w, uy], fill=self.accent, width=max(2, self.size // 14))
            if st.get("strike"):
                sy = y + round(ascent * 0.55)
                d.line([x, sy, x + w, sy], fill=color, width=max(2, self.size // 16))
            x += w

    def _draw_table(self, d: ImageDraw.ImageDraw, img: Image.Image, tb: dict):
        x, y, w, h = tb["x"], tb["y"], tb["w"], tb["h"]
        d.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=self.card)
        ry = y + tb["header_h"]
        for idx, (_, rh) in enumerate(tb["body"]):
            if idx % 2 == 1:
                last = idx == len(tb["body"]) - 1
                if last:
                    d.rounded_rectangle([x + 2, ry, x + w - 2, ry + rh], radius=12,
                                        corners=(False, False, True, True), fill=self.zebra)
                else:
                    d.rectangle([x + 2, ry, x + w - 2, ry + rh], fill=self.zebra)
            ry += rh
        d.rounded_rectangle([x, y, x + w, y + tb["header_h"]], radius=12,
                            corners=(True, True, False, False), fill=self.table_head_bg)

        def draw_row(cells, rh, ry, color, header=False):
            for ci, lines in enumerate(cells):
                col_x, col_w = tb["col_xs"][ci], tb["col_ws"][ci]
                ty = ry + (rh - len(lines) * tb["t_lh"]) / 2
                for line in lines:
                    lw = sum(run[3] for run in line)
                    align = "left"
                    if align == "center":
                        tx = col_x + (col_w - lw) / 2
                    elif align == "right":
                        tx = col_x + col_w - tb["pad_x"] - lw
                    else:
                        tx = col_x + tb["pad_x"]
                    self._draw_line(d, img, tx, round(ty), line, tb["t_lh"], color)
                    ty += tb["t_lh"]

        draw_row(tb["header_cells"], tb["header_h"], y, self.ink, header=True)
        ry = y + tb["header_h"]
        for cells, rh in tb["body"]:
            d.line([x, ry, x + w, ry], fill=self.table_line, width=2)
            draw_row(cells, rh, ry, self.ink)
            ry += rh
        d.rounded_rectangle([x, y, x + w, y + h], radius=12, outline=self.hair, width=2)
