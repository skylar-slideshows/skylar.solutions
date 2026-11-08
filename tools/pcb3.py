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
        mesh = u32(); m.instances.append((mesh, f32s(12)))
    return m

def colour_index(m, rgb):
    rgb = tuple(int(c) for c in rgb)
    if rgb not in m.palette:
        m.palette.append(rgb)
    i = m.palette.index(rgb)
    assert i < 32, "the viewer supports up to 32 colours per model"
    return i

def add_mesh(m, pos, nrm, col, tri, flags=0):
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
    m.instances.append((len(m.meshes) - 1, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0]))
    return len(m.meshes) - 1

def remove_instances(m, indices):
    drop = set(indices)
    m.instances = [inst for i, inst in enumerate(m.instances) if i not in drop]
    used = sorted({mesh for mesh, _ in m.instances})
    remap = {old: new for new, old in enumerate(used)}
    m.meshes = [m.meshes[i] for i in used]
    m.instances = [(remap[mesh], mat) for mesh, mat in m.instances]

def write_model(m, path):
    lo, hi = [1e30] * 3, [-1e30] * 3
    for mesh, mat in m.instances:
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
    for mesh, mat in m.instances:
        out += struct.pack("<I12f", mesh, *mat)
    open(path, "wb").write(out)
    key = os.path.basename(path)
    open(path + ".js", "w").write('(window.__boardModels=window.__boardModels||{})["%s"]="%s";\n' % (key, base64.b64encode(out).decode()))
