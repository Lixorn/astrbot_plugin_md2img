# -*- coding: utf-8 -*-

class RendererTableMixin:
    @staticmethod
    def _normalize_table(b):
        header, rows = list(b["header"]), [list(r) for r in b["rows"]]
        ncol = max([len(header)] + [len(r) for r in rows] + [1])
        header += [""] * (ncol - len(header))
        rows = [r + [""] * (ncol - len(r)) for r in rows]
        aligns = (b["aligns"] + ["left"] * ncol)[:ncol]
        return header, rows, aligns, ncol

    def _table_measure(self, header, rows, ncol, tsize):
        def segs(txt):
            return self._parse_inline(txt) or [(" ", {})]

        hb_w, nat = [], []
        for ci in range(ncol):
            hw = sum(self._style_font(st, tsize, force_bold=True)[0].getlength(t)
                     for t, st in segs(header[ci]))
            bw = 0
            for r_ in rows:
                bw = max(bw, sum(self._style_font(st, tsize)[0].getlength(t)
                                   for t, st in segs(r_[ci])))
            hb_w.append(hw)
            nat.append(max(hw, bw))
        return hb_w, nat

    def table_needs_landscape(self, b) -> bool:
        header, rows, _, ncol = self._normalize_table(b)
        _, nat = self._table_measure(header, rows, ncol, self.size - 2)
        return sum(nat) + 2 * 16 * ncol > self.content_w * self.landscape_ratio

    def _layout_table(self, b, x0, y, cw):
        base_tsize = self.size - 2
        nowrap_floor = max(22, round(self.size * 0.75))
        pad_x, pad_y = 16, 12
        header, rows, aligns, ncol = self._normalize_table(b)

        def cell_segs(txt):
            return self._parse_inline(txt) or [(" ", {})]

        avail = cw - 2 * pad_x * ncol
        tsize = base_tsize
        hb_w, nat = self._table_measure(header, rows, ncol, tsize)
        while sum(nat) > avail and tsize > nowrap_floor:
            tsize -= 2
            hb_w, nat = self._table_measure(header, rows, ncol, tsize)

        if sum(nat) <= avail:
            col_content = list(nat)
            extra = avail - sum(col_content)
            s = sum(col_content) or 1
            col_content = [w_ + extra * w_ / s for w_ in col_content]
        else:
            tsize = base_tsize
            hb_w, nat = self._table_measure(header, rows, ncol, tsize)
            if sum(hb_w) <= avail:
                col_content = list(hb_w)
                left = avail - sum(col_content)
                need = [max(0.0, nat[i] - col_content[i]) for i in range(ncol)]
                tot = sum(need)
                if tot > 0:
                    give = min(left, tot)
                    for i in range(ncol):
                        col_content[i] += need[i] / tot * give
            else:
                s = sum(hb_w) or 1
                col_content = [w_ * avail / s for w_ in hb_w]
            cap = avail * 0.45
            for _ in range(ncol):
                over = 0.0
                for i in range(ncol):
                    limit = max(cap, hb_w[i])
                    if col_content[i] > limit:
                        over += col_content[i] - limit
                        col_content[i] = limit
                if over <= 1:
                    break
                room = [max(0.0, max(cap, hb_w[i]) - col_content[i]) for i in range(ncol)]
                tot = sum(room)
                if tot <= 0:
                    break
                give = min(over, tot)
                for i in range(ncol):
                    col_content[i] += room[i] / tot * give

        col_ws = [w_ + 2 * pad_x for w_ in col_content]
        col_ws[-1] += cw - sum(col_ws)
        col_xs = [x0]
        for w_ in col_ws[:-1]:
            col_xs.append(col_xs[-1] + w_)
        content_ws = [w_ - 2 * pad_x for w_ in col_ws]

        t_lh = round(tsize * 1.5)
        header_cells = [self._wrap(cell_segs(header[ci]), tsize, content_ws[ci],
                                   force_bold=True) for ci in range(ncol)]
        header_h = max(len(c) for c in header_cells) * t_lh + 2 * pad_y
        body = []
        for r_ in rows:
            cells = [self._wrap(cell_segs(r_[ci]), tsize, content_ws[ci])
                     for ci in range(ncol)]
            body.append((cells, max(len(c) for c in cells) * t_lh + 2 * pad_y))
        th = header_h + sum(h_ for _, h_ in body)
        return {"x": x0, "y": y, "w": cw, "h": th, "col_xs": col_xs, "col_ws": col_ws,
                "pad_x": pad_x, "t_lh": t_lh, "header_cells": header_cells,
                "header_h": header_h, "body": body, "aligns": aligns}, th
