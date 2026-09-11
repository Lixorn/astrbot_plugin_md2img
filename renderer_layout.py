# -*- coding: utf-8 -*-

class RendererLayoutMixin:
    def _layout(self, blocks):
        ops = []
        x0 = self.card_margin + self.card_pad
        y = self.card_margin + self.card_pad
        cw = self.content_w
        lh = round(self.size * 1.65)

        head_size = {"h1": round(self.size * 1.55), "h2": round(self.size * 1.28),
                     "h3": round(self.size * 1.12)}
        head_gap_top = {"h1": round(self.size * 1.0), "h2": round(self.size * 0.95),
                        "h3": round(self.size * 0.8)}
        first = True

        for b in blocks:
            t = b["type"]
            if not first:
                if t in head_size:
                    y += head_gap_top[t]
                elif t == "hr":
                    y += round(self.size * 0.85)
                elif t in ("code", "table"):
                    y += round(self.size * 0.7)
                else:
                    y += round(self.size * 0.62)
            first = False

            if t in head_size:
                size = head_size[t]
                lines = self._wrap(b["segments"], size, cw - (30 if t == "h1" else 0), force_bold=True)
                h_lh = round(size * 1.45)
                for j, runs in enumerate(lines):
                    tx = x0
                    if t == "h1":
                        ops.append(("rect", x0, y + round(size * 0.16),
                                    x0 + 10, y + round(size * 0.16) + round(size * 1.0),
                                    5, self.accent))
                        tx = x0 + 30
                    color = self.accent if t == "h3" else self.ink
                    ops.append(("line", tx, y, runs, h_lh, color))
                    y += h_lh
                y += round(self.size * 0.35)

            elif t == "p":
                lines = self._wrap(b["segments"], self.size, cw)
                for runs in lines:
                    ops.append(("line", x0, y, runs, lh, self.ink))
                    y += lh

            elif t == "quote":
                bar_x = x0
                tx = x0 + 26
                lines = self._wrap(b["segments"], self.size, cw - 26)
                top = y
                for runs in lines:
                    ops.append(("line", tx, y, runs, lh, self.sub))
                    y += lh
                if lines:
                    ops.append(("rect", bar_x, top + 4, bar_x + 7, y - lh + round(lh * 0.86),
                                3, self.quote_bar))

            elif t == "li":
                indent = b["depth"] * 30
                tx = x0 + indent + 38
                lines = self._wrap(b["segments"], self.size, cw - indent - 38)
                for j, runs in enumerate(lines):
                    ops.append(("line", tx, y, runs, lh, self.ink))
                    if j == 0:
                        if b["ordered"]:
                            fp = self._font(self.size, 700)
                            ops.append(("text", x0 + indent, y, b["marker"], fp, self.accent))
                        else:
                            by = y + round(lh * 0.42)
                            ops.append(("rect", x0 + indent + 6, by, x0 + indent + 18,
                                        by + 12, 3, self.accent))
                    y += lh

            elif t == "code":
                csize = self.size - 4
                c_lh = round(csize * 1.55)
                pad_x, pad_y = 24, 18
                wrapped = []
                for cl in (b["lines"] or [""]):
                    ws = self._wrap([(cl if cl else " ", {"plain": True})], csize, cw - pad_x * 2)
                    wrapped.extend(ws)
                bh = pad_y * 2 + c_lh * len(wrapped)
                ops.append(("rect", x0, y, x0 + cw, y + bh, 16, self.code_bg))
                cy = y + pad_y
                for runs in wrapped:
                    ops.append(("line", x0 + pad_x, cy, runs, c_lh, self.code_fg))
                    cy += c_lh
                y += bh

            elif t == "hr":
                mid = x0 + cw // 2
                ly = y + round(self.size * 0.5)
                ops.append(("rect", mid - 70, ly, mid + 70, ly + 3, 2, self.accent))
                ops.append(("diamond", mid, ly + 1, 7, self.accent))
                y += round(self.size * 0.5) + 8

            elif t == "table":
                tb, th = self._layout_table(b, x0, y, cw)
                ops.append(("table", tb))
                y += th

            elif t == "note":
                fp = self._font(self.size - 4, 400)
                ny = y + round(self.size * 0.18)
                ops.append(("rect", x0 + 2, ny + 6, x0 + 16, ny + 20, 4, self.accent))
                ops.append(("text", x0 + 26, ny,
                            "表格内容较多，已单独生成横屏图片（见下一条）", fp, self.sub))
                y += round(self.size * 1.2)

            elif t == "imgblock":
                for alt, url in b["items"]:
                    im = self._images.get(url)
                    if im is None:
                        fp = self._font(self.size - 4, 400)
                        ops.append(("text", x0, y,
                                    "[图片加载失败" + ((":" + alt) if alt else "") + "]",
                                    fp, self.sub))
                        y += round(self.size * 1.2)
                        continue
                    w_nat, h_nat = im.size
                    dw = min(cw, w_nat)
                    dh = round(h_nat * dw / w_nat)
                    max_h = round(cw * 1.4)
                    if dh > max_h:
                        dh = max_h
                        dw = round(w_nat * dh / h_nat)
                    dx = x0 + (cw - dw) // 2
                    ops.append(("photo", dx, y, dw, dh, url))
                    y += dh + round(self.size * 0.4)

        return ops, y + self.card_margin + self.card_pad
