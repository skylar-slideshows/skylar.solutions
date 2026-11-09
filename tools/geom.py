"""Small geometry kit for parts the STEP exports don't have (panels, knobs, faders).

Everything builds into a Mesh (positions, normals, palette colours, triangles)
that pcb3.add_mesh() can store. Units are millimetres.
"""
import math


class Mesh:
    def __init__(self):
        self.pos, self.nrm, self.col, self.tri = [], [], [], []

    def vert(self, p, n, c):
        self.pos.append(tuple(p)); self.nrm.append(tuple(n)); self.col.append(c)
        return len(self.pos) - 1

    def add(self, other):
        base = len(self.pos)
        self.pos += other.pos; self.nrm += other.nrm; self.col += other.col
        self.tri += [base + i for i in other.tri]
        return self

    def moved(self, dx, dy, dz):
        out = Mesh()
        out.pos = [(x + dx, y + dy, z + dz) for x, y, z in self.pos]
        out.nrm, out.col, out.tri = list(self.nrm), list(self.col), list(self.tri)
        return out


def _unit(v):
    l = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / l for c in v)


def quad(m, a, b, c, d, n, ci):
    i = [m.vert(p, n, ci) for p in (a, b, c, d)]
    m.tri += [i[0], i[1], i[2], i[0], i[2], i[3]]


def box(lo, hi, ci, m=None):
    m = m or Mesh()
    a, b = lo, hi
    quad(m, (b[0], a[1], a[2]), (b[0], b[1], a[2]), (b[0], b[1], b[2]), (b[0], a[1], b[2]), (1, 0, 0), ci)
    quad(m, (a[0], b[1], a[2]), (a[0], a[1], a[2]), (a[0], a[1], b[2]), (a[0], b[1], b[2]), (-1, 0, 0), ci)
    quad(m, (b[0], b[1], a[2]), (a[0], b[1], a[2]), (a[0], b[1], b[2]), (b[0], b[1], b[2]), (0, 1, 0), ci)
    quad(m, (a[0], a[1], a[2]), (b[0], a[1], a[2]), (b[0], a[1], b[2]), (a[0], a[1], b[2]), (0, -1, 0), ci)
    quad(m, (a[0], a[1], b[2]), (b[0], a[1], b[2]), (b[0], b[1], b[2]), (a[0], b[1], b[2]), (0, 0, 1), ci)
    quad(m, (a[0], b[1], a[2]), (b[0], b[1], a[2]), (b[0], a[1], a[2]), (a[0], a[1], a[2]), (0, 0, -1), ci)
    return m


def arc_points(cx, cy, r, a0, a1, step_deg=10.0):
    """Points along a CCW arc from a0 to a1 (radians), both ends included."""
    while a1 <= a0: a1 += 2 * math.pi
    n = max(2, int(math.ceil(math.degrees(a1 - a0) / step_deg)))
    return [(cx + r * math.cos(a0 + (a1 - a0) * k / n), cy + r * math.sin(a0 + (a1 - a0) * k / n)) for k in range(n + 1)]


def circle(cx, cy, r, segs=24):
    return [(cx + r * math.cos(2 * math.pi * k / segs), cy + r * math.sin(2 * math.pi * k / segs)) for k in range(segs)]


def rrect(cx, cy, w, h, r, step_deg=22.5):
    """Rounded rectangle, counter-clockwise."""
    r = min(r, w / 2 - 1e-6, h / 2 - 1e-6)
    pts = []
    for (qx, qy, a0) in ((w / 2 - r, -h / 2 + r, -math.pi / 2), (w / 2 - r, h / 2 - r, 0),
                         (-w / 2 + r, h / 2 - r, math.pi / 2), (-w / 2 + r, -h / 2 + r, math.pi)):
        pts += arc_points(cx + qx, cy + qy, r, a0, a0 + math.pi / 2, step_deg)[:-1] if r > 0 else [(cx + qx, cy + qy)]
    return pts


def area(loop):
    """Signed area, positive when counter-clockwise (y up)."""
    return 0.5 * sum(loop[i - 1][0] * loop[i][1] - loop[i][0] * loop[i - 1][1] for i in range(len(loop)))


def extrude(outer, holes, z0, z1, ci, walls=True):
    """A flat plate between z0 and z1 with the given outline and holes."""
    outer = outer if area(outer) > 0 else outer[::-1]          # CCW
    holes = [h if area(h) < 0 else h[::-1] for h in holes]     # CW
    flat, hole_idx, ring = [], [], []
    for loop in [outer] + holes:
        if ring: hole_idx.append(len(flat) // 2)
        for x, y in loop: flat += [x, y]
        ring.append(loop)
    tris = earcut(flat, hole_idx)
    pts = [p for loop in ring for p in loop]
    m = Mesh()
    top = [m.vert((x, y, z1), (0, 0, 1), ci) for x, y in pts]
    bot = [m.vert((x, y, z0), (0, 0, -1), ci) for x, y in pts]
    for t in range(0, len(tris), 3):
        a, b, c = tris[t:t + 3]
        (ax, ay), (bx, by), (cx, cy) = pts[a], pts[b], pts[c]
        if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0: b, c = c, b   # top faces up
        m.tri += [top[a], top[b], top[c], bot[a], bot[c], bot[b]]
    if walls:
        for loop in ring:
            for i in range(len(loop)):
                (x0, y0), (x1, y1) = loop[i], loop[(i + 1) % len(loop)]
                n = _unit((y1 - y0, -(x1 - x0), 0))
                quad(m, (x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1), n, ci)
    return m


def lathe(profile, ci, segs=32, axis=(0.0, 0.0)):
    """Revolve a (radius, z) profile around a vertical axis. Each profile segment
    gets its own ring of vertices so creases stay sharp."""
    m = Mesh()
    cx, cy = axis
    for (r0, z0), (r1, z1) in zip(profile, profile[1:]):
        dr, dz = r1 - r0, z1 - z0
        nr, nz = _unit((dz, -dr))
        ring0, ring1 = [], []
        for k in range(segs + 1):
            a = 2 * math.pi * k / segs
            ca, sa = math.cos(a), math.sin(a)
            n = (nr * ca, nr * sa, nz)
            ring0.append(m.vert((cx + r0 * ca, cy + r0 * sa, z0), n, ci))
            ring1.append(m.vert((cx + r1 * ca, cy + r1 * sa, z1), n, ci))
        for k in range(segs):
            m.tri += [ring0[k], ring0[k + 1], ring1[k + 1], ring0[k], ring1[k + 1], ring1[k]]
    return m


def cylinder_y(cx, cz, r, y0, y1, ci, segs=20):
    """A capped cylinder lying along the y axis."""
    m = Mesh()
    ring0, ring1 = [], []
    for k in range(segs + 1):
        a = 2 * math.pi * k / segs
        n = (math.cos(a), 0, math.sin(a))
        ring0.append(m.vert((cx + r * n[0], y0, cz + r * n[2]), n, ci))
        ring1.append(m.vert((cx + r * n[0], y1, cz + r * n[2]), n, ci))
    for k in range(segs):
        m.tri += [ring0[k], ring1[k], ring1[k + 1], ring0[k], ring1[k + 1], ring0[k + 1]]
    for y, ny in ((y0, -1), (y1, 1)):
        c = m.vert((cx, y, cz), (0, ny, 0), ci)
        rim = [m.vert((cx + r * math.cos(2 * math.pi * k / segs), y, cz + r * math.sin(2 * math.pi * k / segs)), (0, ny, 0), ci) for k in range(segs)]
        for k in range(segs):
            m.tri += [c, rim[k], rim[(k + 1) % segs]] if ny < 0 else [c, rim[(k + 1) % segs], rim[k]]
    return m


# --------------------------------------------------------------------------
# earcut (port of mapbox/earcut 2.2, without the z-order hash)
# --------------------------------------------------------------------------
class _N:
    __slots__ = ("i", "x", "y", "prev", "next", "steiner")

    def __init__(self, i, x, y):
        self.i, self.x, self.y = i, x, y
        self.prev = self.next = None
        self.steiner = False


def earcut(data, hole_indices=()):
    has_holes = bool(hole_indices)
    outer_len = hole_indices[0] * 2 if has_holes else len(data)
    outer = _linked(data, 0, outer_len, True)
    tris = []
    if outer is None or outer.next is outer.prev: return tris
    if has_holes: outer = _eliminate_holes(data, hole_indices, outer)
    _earcut_linked(outer, tris, 0)
    return tris


def _signed_area(data, start, end):
    s, j = 0.0, end - 2
    for i in range(start, end, 2):
        s += (data[j] - data[i]) * (data[i + 1] + data[j + 1]); j = i
    return s


def _linked(data, start, end, clockwise):
    last = None
    if clockwise == (_signed_area(data, start, end) > 0):
        for i in range(start, end, 2): last = _insert(i // 2, data[i], data[i + 1], last)
    else:
        for i in range(end - 2, start - 2, -2): last = _insert(i // 2, data[i], data[i + 1], last)
    if last is not None and _equals(last, last.next):
        _remove(last); last = last.next
    return last


def _filter(start, end=None):
    if start is None: return start
    end = end or start
    p = start
    while True:
        again = False
        if not p.steiner and (_equals(p, p.next) or _area(p.prev, p, p.next) == 0):
            _remove(p); p = end = p.prev
            if p is p.next: break
            again = True
        else:
            p = p.next
        if not (again or p is not end): break
    return end


def _earcut_linked(ear, tris, pass_):
    if ear is None: return
    stop = ear
    while ear.prev is not ear.next:
        prev, nxt = ear.prev, ear.next
        if _is_ear(ear):
            tris += [prev.i, ear.i, nxt.i]
            _remove(ear)
            ear = nxt.next; stop = nxt.next
            continue
        ear = nxt
        if ear is stop:
            if pass_ == 0: _earcut_linked(_filter(ear), tris, 1)
            elif pass_ == 1:
                ear = _cure(_filter(ear), tris)
                _earcut_linked(ear, tris, 2)
            elif pass_ == 2: _split(ear, tris)
            break


def _is_ear(ear):
    a, b, c = ear.prev, ear, ear.next
    if _area(a, b, c) >= 0: return False
    p = ear.next.next
    while p is not ear.prev:
        if _in_tri(a.x, a.y, b.x, b.y, c.x, c.y, p.x, p.y) and _area(p.prev, p, p.next) >= 0: return False
        p = p.next
    return True


def _cure(start, tris):
    p = start
    while True:
        a, b = p.prev, p.next.next
        if not _equals(a, b) and _intersects(a, p, p.next, b) and _locally_inside(a, b) and _locally_inside(b, a):
            tris += [a.i, p.i, b.i]
            _remove(p); _remove(p.next)
            p = start = b
        p = p.next
        if p is start: break
    return _filter(p)


def _split(start, tris):
    a = start
    while True:
        b = a.next.next
        while b is not a.prev:
            if a.i != b.i and _is_valid_diagonal(a, b):
                c = _split_polygon(a, b)
                a = _filter(a, a.next); c = _filter(c, c.next)
                _earcut_linked(a, tris, 0); _earcut_linked(c, tris, 0)
                return
            b = b.next
        a = a.next
        if a is start: break


def _eliminate_holes(data, hole_indices, outer):
    queue = []
    for k, h in enumerate(hole_indices):
        start = h * 2
        end = hole_indices[k + 1] * 2 if k + 1 < len(hole_indices) else len(data)
        lst = _linked(data, start, end, False)
        if lst is None: continue
        if lst is lst.next: lst.steiner = True
        queue.append(_leftmost(lst))
    queue.sort(key=lambda n: n.x)
    for h in queue:
        outer = _eliminate_hole(h, outer)
    return outer


def _eliminate_hole(hole, outer):
    bridge = _find_bridge(hole, outer)
    if bridge is None: return outer
    rev = _split_polygon(bridge, hole)
    _filter(rev, rev.next)
    return _filter(bridge, bridge.next)


def _find_bridge(hole, outer):
    p = outer
    hx, hy = hole.x, hole.y
    qx, m = -math.inf, None
    while True:
        if hy <= p.y and hy >= p.next.y and p.next.y != p.y:
            x = p.x + (hy - p.y) * (p.next.x - p.x) / (p.next.y - p.y)
            if hx >= x > qx:
                qx = x
                m = p if p.x < p.next.x else p.next
                if x == hx: return m
        p = p.next
        if p is outer: break
    if m is None: return None
    stop = m
    mx, my = m.x, m.y
    tan_min = math.inf
    p = m
    while True:
        if hx >= p.x >= mx and hx != p.x and _in_tri(hx if hy < my else qx, hy, mx, my, qx if hy < my else hx, hy, p.x, p.y):
            tan = abs(hy - p.y) / (hx - p.x)
            if _locally_inside(p, hole) and (tan < tan_min or (tan == tan_min and (p.x > m.x or (p.x == m.x and _sector_contains(m, p))))):
                m, tan_min = p, tan
        p = p.next
        if p is stop: break
    return m


def _sector_contains(m, p):
    return _area(m.prev, m, p.prev) < 0 and _area(p.next, m, m.next) < 0


def _leftmost(start):
    p, left = start, start
    while True:
        if p.x < left.x or (p.x == left.x and p.y < left.y): left = p
        p = p.next
        if p is start: return left


def _in_tri(ax, ay, bx, by, cx, cy, px, py):
    return ((cx - px) * (ay - py) >= (ax - px) * (cy - py) and
            (ax - px) * (by - py) >= (bx - px) * (ay - py) and
            (bx - px) * (cy - py) >= (cx - px) * (by - py))


def _is_valid_diagonal(a, b):
    return (a.next.i != b.i and a.prev.i != b.i and not _intersects_polygon(a, b) and
            ((_locally_inside(a, b) and _locally_inside(b, a) and _middle_inside(a, b) and
              (_area(a.prev, a, b.prev) or _area(a, b.prev, b))) or
             (_equals(a, b) and _area(a.prev, a, a.next) > 0 and _area(b.prev, b, b.next) > 0)))


def _area(p, q, r):
    return (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)


def _equals(a, b):
    return a.x == b.x and a.y == b.y


def _sign(v):
    return (v > 0) - (v < 0)


def _on_segment(p, q, r):
    return min(p.x, r.x) <= q.x <= max(p.x, r.x) and min(p.y, r.y) <= q.y <= max(p.y, r.y)


def _intersects(p1, q1, p2, q2):
    o1, o2 = _sign(_area(p1, q1, p2)), _sign(_area(p1, q1, q2))
    o3, o4 = _sign(_area(p2, q2, p1)), _sign(_area(p2, q2, q1))
    if o1 != o2 and o3 != o4: return True
    if o1 == 0 and _on_segment(p1, p2, q1): return True
    if o2 == 0 and _on_segment(p1, q2, q1): return True
    if o3 == 0 and _on_segment(p2, p1, q2): return True
    if o4 == 0 and _on_segment(p2, q1, q2): return True
    return False


def _intersects_polygon(a, b):
    p = a
    while True:
        if p.i != a.i and p.next.i != a.i and p.i != b.i and p.next.i != b.i and _intersects(p, p.next, a, b): return True
        p = p.next
        if p is a: return False


def _locally_inside(a, b):
    if _area(a.prev, a, a.next) < 0:
        return _area(a, b, a.next) >= 0 and _area(a, a.prev, b) >= 0
    return _area(a, b, a.prev) < 0 or _area(a, a.next, b) < 0


def _middle_inside(a, b):
    p, inside = a, False
    px, py = (a.x + b.x) / 2, (a.y + b.y) / 2
    while True:
        if ((p.y > py) != (p.next.y > py)) and p.next.y != p.y and px < (p.next.x - p.x) * (py - p.y) / (p.next.y - p.y) + p.x:
            inside = not inside
        p = p.next
        if p is a: return inside


def _split_polygon(a, b):
    a2, b2 = _N(a.i, a.x, a.y), _N(b.i, b.x, b.y)
    an, bp = a.next, b.prev
    a.next = b; b.prev = a
    a2.next = an; an.prev = a2
    b2.next = a2; a2.prev = b2
    bp.next = b2; b2.prev = bp
    return b2


def _insert(i, x, y, last):
    p = _N(i, x, y)
    if last is None:
        p.prev = p.next = p
    else:
        p.next = last.next; p.prev = last
        last.next.prev = p; last.next = p
    return p


def _remove(p):
    p.next.prev = p.prev
    p.prev.next = p.next
