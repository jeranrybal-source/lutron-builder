"""Counting lighting symbols out of a vector PDF, matched against the legend.

The idea, in one line: a lighting PDF exported from CAD is the drawing, not a
picture of it, so counting fittings is arithmetic.

Three steps:

  1. CLUSTER   -- a symbol is several touching paths (a circle and a cross), so
                  group paths that all but touch into one symbol instance.
  2. FINGERPRINT -- describe each cluster in a way that does not change when the
                  symbol is ROTATED, because a wall light turned 90 degrees is
                  the same fitting. Position and angle out; form and size in.
  3. MATCH     -- a legend entry is a symbol sitting next to a short code, whose
                  shape also appears out on the plan. That pairing turns
                  "this shape occurs 47 times" into "47 of type F4".

Everything here is measurement. Nothing is inferred, and nothing is asked of a
language model -- which is the point: counting many identical small marks is
the one thing it reliably cannot do.
"""
from __future__ import annotations

import collections
import math
import re

CELL = 4.0          # pt, grid bucket for the proximity search
TOUCH = 0.6         # pt, how close two paths must be to belong to one symbol
MAX_OBJ = 16.0      # pt, bigger than this is architecture, not a fitting
MIN_SYM, MAX_SYM = 1.5, 14.0        # pt, plausible symbol footprint
# A fixture code as designers actually write them: A, F4, L1a, LB2, EXT-A, MN.
CODE = re.compile(r"^[A-Z]{1,4}[-]?[0-9]{0,3}[a-z]?$")


def bbox(o):
    pts = o.get("pts") or []
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _clusters(objs):
    """Union-find over a grid, merging only paths that genuinely all but touch."""
    parent = list(range(len(objs)))

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    boxes = [bbox(o) for o in objs]
    cells = collections.defaultdict(list)
    for i, b in enumerate(boxes):
        cells[(int(b[0] // CELL), int(b[1] // CELL))].append(i)

    def near(a, b):
        ax0, ay0, ax1, ay1 = boxes[a]
        bx0, by0, bx1, by1 = boxes[b]
        return (ax0 - TOUCH <= bx1 and bx0 - TOUCH <= ax1
                and ay0 - TOUCH <= by1 and by0 - TOUCH <= ay1)

    for (cx, cy), members in cells.items():
        neigh = list(members)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    neigh += cells.get((cx + dx, cy + dy), [])
        for i in members:
            for j in neigh:
                if i < j and near(i, j):
                    ra, rb = find(i), find(j)
                    if ra != rb:
                        parent[rb] = ra

    groups = collections.defaultdict(list)
    for i in range(len(objs)):
        groups[find(i)].append(i)
    return list(groups.values())


def _fingerprint(objs, idxs, bins=1):
    """A symbol's form, unchanged by where it sits OR which way it is turned.

    Rotation invariance matters: the same wall light appears at every angle on
    a plan, and a fingerprint keyed on x/y offsets would call each rotation a
    different fitting. So describe the symbol by quantities that survive a
    rotation -- how many parts, how big overall, and how far each part sits
    from the middle -- rather than by where the parts are.
    """
    boxes = [bbox(objs[i]) for i in idxs]
    cx = sum((b[0] + b[2]) / 2 for b in boxes) / len(boxes)
    cy = sum((b[1] + b[3]) / 2 for b in boxes) / len(boxes)
    radii = sorted(round(math.hypot((b[0] + b[2]) / 2 - cx,
                                    (b[1] + b[3]) / 2 - cy), bins)
                   for b in boxes)
    kinds = tuple(sorted(collections.Counter(objs[i]["object_type"]
                                             for i in idxs).items()))
    # The diagonal, not width x height: a rotated symbol swaps those two.
    w = max(b[2] for b in boxes) - min(b[0] for b in boxes)
    h = max(b[3] for b in boxes) - min(b[1] for b in boxes)
    return (kinds, round(math.hypot(w, h), bins), tuple(radii))


def symbols(page):
    """Every candidate symbol on the page: its fingerprint and its centre."""
    objs = []
    for kind in ("curves", "lines", "rects"):
        for o in getattr(page, kind):
            x0, y0, x1, y1 = bbox(o)
            if (x1 - x0) <= MAX_OBJ and (y1 - y0) <= MAX_OBJ:
                objs.append(o)
    out = []
    for g in _clusters(objs):
        if not 1 <= len(g) <= 60:
            continue
        boxes = [bbox(objs[i]) for i in g]
        w = max(b[2] for b in boxes) - min(b[0] for b in boxes)
        h = max(b[3] for b in boxes) - min(b[1] for b in boxes)
        if not (MIN_SYM <= max(w, h) <= MAX_SYM):
            continue
        out.append({
            "fp": _fingerprint(objs, g),
            "x": sum((b[0] + b[2]) / 2 for b in boxes) / len(boxes),
            "y": sum((b[1] + b[3]) / 2 for b in boxes) / len(boxes),
            "w": w, "h": h, "parts": len(g),
        })
    return out


def legend(page, syms, max_gap=30.0, min_entries=3):
    """Pair fixture codes with the symbol drawn beside them.

    Matching "a short word with a symbol near it" is not enough: a drawing is
    full of notes, and words like MUST, VIEW and MATT pair happily with
    whatever geometry happens to sit beside them. What actually distinguishes a
    legend is that it is a COLUMN -- a run of entries whose symbol sits at the
    same offset from its label, one under another. That is true of every
    designer's legend and of no block of prose, and it needs no word list.

    So: collect every plausible (code, symbol) pair with its offset, then keep
    only the pairs that share an offset with several others. The shape must
    also occur out on the plan, or it is a legend for something not drawn here.
    """
    counts = collections.Counter(s["fp"] for s in syms)
    pairs = []
    for w in page.extract_words():
        text = w["text"].strip()
        if not CODE.match(text):
            continue
        wx = (w["x0"] + w["x1"]) / 2
        wy = page.height - (w["top"] + w["bottom"]) / 2     # to PDF space
        for s in syms:
            dx, dy = wx - s["x"], wy - s["y"]
            if abs(dy) <= 4.0 and 0 < dx <= max_gap and counts[s["fp"]] >= 2:
                pairs.append((round(dx), text, s["fp"], dx))

    # The modal offset IS the legend's own layout.
    by_dx = collections.Counter(p[0] for p in pairs)
    if not by_dx:
        return {}
    best_dx, n = by_dx.most_common(1)[0]
    if n < min_entries:
        return {}
    found = {}
    for dx_r, text, fp, dx in pairs:
        if abs(dx_r - best_dx) <= 2:
            prev = found.get(text)
            if prev is None or dx < prev[0]:
                found[text] = (dx, fp)
    return {code: fp for code, (_d, fp) in found.items()}
