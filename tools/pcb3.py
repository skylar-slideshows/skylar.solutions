"""Read and write the PCB3 model files made by tools/step-to-mesh.mjs.

Used by the add-* scripts to patch a converted model (remove parts, add meshes)
without re-running the STEP conversion.
"""
import base64, os, struct

class Model:
    def __init__(self):
        self.palette, self.meshes, self.instances = [], [], []

def read_model(path):
    b = open(path, "rb").read()
    o = 0
    def u32():
        nonlocal o; v = struct.unpack_from("<I", b, o)[0]; o += 4; return v
    def f32s(n):
        nonlocal o; v = list(struct.unpack_from(f"<{n}f", b, o)); o += 4 * n; return v
    assert b[:4] == b"PCB3", "not a PCB3 model"
    o = 8
    m = Model()
    for _ in range(u32()):
        m.palette.append(tuple(b[o:o + 3])); o += 4
    f32s(6)  # bounds are recomputed on write
    for _ in range(u32()):
        flags = u32(); qmin = f32s(3); qstep = f32s(3); chunks = []
        for _ in range(u32()):
            vc, ic = u32(), u32()
            pos = b[o:o + vc * 6]; o += vc * 6; o = (o + 3) & ~3
            nc = b[o:o + vc * 4]; o += vc * 4
            idx = b[o:o + ic * 2]; o += ic * 2; o = (o + 3) & ~3
            chunks.append((vc, ic, pos, nc, idx))
        m.meshes.append({"flags": flags, "qmin": qmin, "qstep": qstep, "chunks": chunks})
    for _ in range(u32()):
        raw = u32(); m.instances.append((raw & 0xFFFFFF, f32s(12), raw >> 24))
    return m

def colour_index(m, rgb):
    rgb = tuple(int(c) for c in rgb)
    if rgb not in m.palette:
        m.palette.append(rgb)
    i = m.palette.index(rgb)
    assert i < 64, "the viewer supports up to 64 colours per model"
    return i

def add_mesh(m, pos, nrm, col, tri, flags=0, group=0, instance=True):
    """pos/nrm: lists of (x, y, z); col: palette index per vertex; tri: flat index list."""
    lo = [min(p[k] for p in pos) for k in range(3)]
    hi = [max(p[k] for p in pos) for k in range(3)]
    step = [max(hi[k] - lo[k], 1e-9) / 65535 for k in range(3)]
    chunks, vmap, verts, ind = [], {}, [], []
    def flush():
        nonlocal vmap, verts, ind
        if ind:
            P = b"".join(struct.pack("<3H", *[round((pos[g][k] - lo[k]) / step[k]) for k in range(3)]) for g in verts)
            N = b"".join(struct.pack("<4b", *[max(-127, min(127, round(nrm[g][k] * 127))) for k in range(3)], col[g]) for g in verts)
            I = struct.pack(f"<{len(ind)}H", *ind)
            chunks.append((len(verts), len(ind), P, N, I))
        vmap, verts, ind = {}, [], []
    for t in range(0, len(tri), 3):
        need = sum(1 for g in tri[t:t + 3] if g not in vmap)
        if len(verts) + need > 65535: flush()
        for g in tri[t:t + 3]:
            if g not in vmap: vmap[g] = len(verts); verts.append(g)
            ind.append(vmap[g])
    flush()
    m.meshes.append({"flags": flags, "qmin": lo, "qstep": step, "chunks": chunks})
    if instance:
        m.instances.append((len(m.meshes) - 1, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0], group))
    return len(m.meshes) - 1

def remove_instances(m, indices):
    drop = set(indices)
    m.instances = [inst for i, inst in enumerate(m.instances) if i not in drop]
    used = sorted({inst[0] for inst in m.instances})
    remap = {old: new for new, old in enumerate(used)}
    m.meshes = [m.meshes[i] for i in used]
    m.instances = [(remap[inst[0]], inst[1], inst[2] if len(inst) > 2 else 0) for inst in m.instances]

def write_model(m, path):
    lo, hi = [1e30] * 3, [-1e30] * 3
    for mesh, mat, *_ in m.instances:
        q, s = m.meshes[mesh]["qmin"], m.meshes[mesh]["qstep"]
        for c in range(8):
            p = [q[k] + (s[k] * 65535 if c >> k & 1 else 0) for k in range(3)]
            w = [mat[r * 4] * p[0] + mat[r * 4 + 1] * p[1] + mat[r * 4 + 2] * p[2] + mat[r * 4 + 3] for r in range(3)]
            lo = [min(lo[k], w[k]) for k in range(3)]; hi = [max(hi[k], w[k]) for k in range(3)]
    out = bytearray(b"PCB3" + struct.pack("<I", 1))
    out += struct.pack("<I", len(m.palette)) + b"".join(bytes([*c, 0]) for c in m.palette)
    out += struct.pack("<6f", *lo, *hi) + struct.pack("<I", len(m.meshes))
    for mesh in m.meshes:
        out += struct.pack("<I3f3fI", mesh["flags"], *mesh["qmin"], *mesh["qstep"], len(mesh["chunks"]))
        for vc, ic, P, N, I in mesh["chunks"]:
            out += struct.pack("<II", vc, ic) + P
            out += b"\0" * (-len(out) % 4)
            out += N + I
            out += b"\0" * (-len(out) % 4)
    out += struct.pack("<I", len(m.instances))
    for inst in m.instances:
        group = inst[2] if len(inst) > 2 else 0
        out += struct.pack("<I12f", inst[0] | (group << 24), *inst[1])
    open(path, "wb").write(out)
    key = os.path.basename(path)
    open(path + ".js", "w").write('(window.__boardModels=window.__boardModels||{})["%s"]="%s";\n' % (key, base64.b64encode(out).decode()))


def mat_mul(a, b):
    """3x4 row-major rigid transforms: a after b."""
    o = [0.0] * 12
    for r in range(3):
        for c in range(4):
            o[r * 4 + c] = a[r * 4] * b[c] + a[r * 4 + 1] * b[4 + c] + a[r * 4 + 2] * b[8 + c] + (a[r * 4 + 3] if c == 3 else 0)
    return o

def translate(x, y, z):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z]

def recolour(m, mesh_index, mapping=None, to=None):
    """Change vertex colours of one mesh: mapping {old index: new index}, or everything to `to`."""
    mesh = m.meshes[mesh_index]
    chunks = []
    for vc, ic, P, N, I in mesh["chunks"]:
        n = bytearray(N)
        for v in range(vc):
            c = n[v * 4 + 3]
            if to is not None: n[v * 4 + 3] = to
            elif mapping and c in mapping: n[v * 4 + 3] = mapping[c]
        chunks.append((vc, ic, P, bytes(n), I))
    mesh["chunks"] = chunks

def merge(dst, src, transform=None, group=0, keep=None):
    """Add src's meshes and instances to dst. keep(instance_index) can filter instances."""
    pal = {i: colour_index(dst, c) for i, c in enumerate(src.palette)}
    base = len(dst.meshes)
    used = sorted({inst[0] for i, inst in enumerate(src.instances) if keep is None or keep(i)})
    remap = {old: base + k for k, old in enumerate(used)}
    for old in used:
        mesh = dict(src.meshes[old])
        chunks = []
        for vc, ic, P, N, I in mesh["chunks"]:
            n = bytearray(N)
            for v in range(vc): n[v * 4 + 3] = pal[n[v * 4 + 3]]
            chunks.append((vc, ic, P, bytes(n), I))
        mesh["chunks"] = chunks
        dst.meshes.append(mesh)
    for i, inst in enumerate(src.instances):
        if keep is not None and not keep(i): continue
        mat = mat_mul(transform, inst[1]) if transform else inst[1]
        dst.instances.append((remap[inst[0]], mat, group))


def mesh_points(m, k):
    """Decoded local vertex positions of mesh k, one list per chunk."""
    mesh = m.meshes[k]
    out = []
    for vc, ic, P, N, I in mesh["chunks"]:
        q = struct.unpack(f"<{vc * 3}H", P)
        out.append([[mesh["qmin"][a] + q[v * 3 + a] * mesh["qstep"][a] for a in range(3)] for v in range(vc)])
    return out


def deform(m, k, fn):
    """Move mesh k's vertices: fn(point, colour index) -> new point (local coordinates).
    Triangles and normals stay as they are; positions are re-quantized."""
    mesh = m.meshes[k]
    pts = mesh_points(m, k)
    new = [[fn(p, N[v * 4 + 3]) for v, p in enumerate(chunk)] for chunk, (vc, ic, P, N, I) in zip(pts, mesh["chunks"])]
    flat = [p for chunk in new for p in chunk]
    lo = [min(p[a] for p in flat) for a in range(3)]
    hi = [max(p[a] for p in flat) for a in range(3)]
    step = [max(hi[a] - lo[a], 1e-9) / 65535 for a in range(3)]
    chunks = []
    for chunk, (vc, ic, P, N, I) in zip(new, mesh["chunks"]):
        P2 = b"".join(struct.pack("<3H", *[round((p[a] - lo[a]) / step[a]) for a in range(3)]) for p in chunk)
        chunks.append((vc, ic, P2, N, I))
    mesh.update(qmin=lo, qstep=step, chunks=chunks)


def compact_palette(m):
    """Drop palette colours no vertex uses (recolouring leaves the old ones behind)."""
    used = set()
    for mesh in m.meshes:
        for vc, ic, P, N, I in mesh["chunks"]:
            used.update(N[v * 4 + 3] for v in range(vc))
    keep = sorted(used)
    remap = {old: new for new, old in enumerate(keep)}
    m.palette = [m.palette[i] for i in keep]
    for mesh in m.meshes:
        chunks = []
        for vc, ic, P, N, I in mesh["chunks"]:
            n = bytearray(N)
            for v in range(vc): n[v * 4 + 3] = remap[n[v * 4 + 3]]
            chunks.append((vc, ic, P, bytes(n), I))
        mesh["chunks"] = chunks
    return m
