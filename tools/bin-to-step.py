#!/usr/bin/env python3
"""Write a viewer model (.bin) out as a STEP file with tessellated (triangle) geometry.

    python3 tools/bin-to-step.py MODEL.bin OUT.step "Product name" ["1=LED rings" "2=Front panel" ...]

The geometry is exactly what the site shows: the optimised meshes with every added or
redrawn part and fix. It's written as STEP AP242 triangulated faces with their colours,
in millimetres. Each distinct part is written once and placed wherever it occurs through
a standard assembly structure (as in KiCad's exports), and the model's show/hide groups
become named sub-assemblies. There is no exact (B-rep) geometry: these are meshes.
Readers built on OpenCASCADE 7.6 or later (FreeCAD, KiCad's 3D viewer and others)
open tessellated STEP.
"""
import struct, sys, datetime
from pcb3 import read_model


def real(x):
    s = f"{x:.4f}".rstrip("0")
    return "0." if s in ("-0.", "0.") else s


def esc(s):
    return s.replace("'", "''")


class Step:
    def __init__(self):
        self.ents = []

    def add(self, text):
        self.ents.append(text)
        return len(self.ents)

    def reserve(self):
        self.ents.append(None)
        return len(self.ents)

    def put(self, n, text):
        self.ents[n - 1] = text


def mesh_geometry(mesh):
    pos, col, tri = [], [], []
    for vc, ic, P, N, I in mesh["chunks"]:
        base = len(pos)
        q = struct.unpack(f"<{vc * 3}H", P)
        for v in range(vc):
            pos.append(tuple(mesh["qmin"][k] + q[v * 3 + k] * mesh["qstep"][k] for k in range(3)))
            col.append(N[v * 4 + 3])
        idx = struct.unpack(f"<{ic}H", I)
        tri += [base + i for i in idx]
    return pos, col, tri


def main(bin_path, out_path, product, *group_args):
    groups = {}
    for g in group_args:
        k, name = g.split("=", 1)
        groups[int(k)] = name
    m = read_model(bin_path)
    st = Step()
    ac = st.add("APPLICATION_CONTEXT('managed model based 3d engineering')")
    st.add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','ap242_managed_model_based_3d_engineering',2014,#{ac})")
    pc = st.add(f"PRODUCT_CONTEXT('',#{ac},'mechanical')")
    pdc = st.add(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{ac},'design')")
    mm = st.add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
    rad = st.add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
    sr = st.add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
    unc = st.add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-04),#{mm},'distance_accuracy_value','confusion accuracy')")
    ctx = st.add(f"( GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{unc})) "
                 f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{mm},#{rad},#{sr})) REPRESENTATION_CONTEXT('','3D') )")
    o = st.add("CARTESIAN_POINT('',(0.,0.,0.))")
    dz = st.add("DIRECTION('',(0.,0.,1.))")
    dx = st.add("DIRECTION('',(1.,0.,0.))")

    def origin_axis():
        return st.add(f"AXIS2_PLACEMENT_3D('',#{o},#{dz},#{dx})")

    def product_def(name):
        p = st.add(f"PRODUCT('{esc(name)}','{esc(name)}','',(#{pc}))")
        pdf = st.add(f"PRODUCT_DEFINITION_FORMATION('','',#{p})")
        pd = st.add(f"PRODUCT_DEFINITION('design','',#{pdf},#{pdc})")
        pds = st.add(f"PRODUCT_DEFINITION_SHAPE('','',#{pd})")
        return pd, pds

    # colours: one style chain per palette entry, shared by all faces of that colour
    styles = {}
    def style(ci):
        if ci not in styles:
            r, g, b = (c / 255 for c in m.palette[ci])
            c = st.add(f"COLOUR_RGB('',{real(r)},{real(g)},{real(b)})")
            fasc = st.add(f"FILL_AREA_STYLE_COLOUR('',#{c})")
            fas = st.add(f"FILL_AREA_STYLE('',(#{fasc}))")
            ssfa = st.add(f"SURFACE_STYLE_FILL_AREA(#{fas})")
            sss = st.add(f"SURFACE_SIDE_STYLE('',(#{ssfa}))")
            ssu = st.add(f"SURFACE_STYLE_USAGE(.BOTH.,#{sss})")
            styles[ci] = st.add(f"PRESENTATION_STYLE_ASSIGNMENT((#{ssu}))")
        return styles[ci]

    styled = []
    # parts: each mesh once
    used = sorted({inst[0] for inst in m.instances})
    part = {}
    for k in used:
        pos, col, tri = mesh_geometry(m.meshes[k])
        if not tri: continue
        coords = st.add("COORDINATES_LIST('',%d,(%s))" % (len(pos), ",".join(f"({real(x)},{real(y)},{real(z)})" for x, y, z in pos)))
        by_colour = {}
        for t in range(0, len(tri), 3):
            by_colour.setdefault(col[tri[t]], []).append(tri[t:t + 3])
        faces = []
        for ci, ts in by_colour.items():
            pts = sorted({i for t in ts for i in t})
            local = {g: n + 1 for n, g in enumerate(pts)}
            f = st.add("TRIANGULATED_FACE('',#%d,%d,(),$,(%s),(%s))" % (
                coords, len(pos), ",".join(str(i + 1) for i in pts),
                ",".join(f"({local[a]},{local[b]},{local[c]})" for a, b, c in ts)))
            faces.append(f)
            styled.append(st.add(f"STYLED_ITEM('color',(#{style(ci)}),#{f})"))
        shell = st.add("TESSELLATED_SHELL('',(%s),$)" % ",".join(f"#{f}" for f in faces))
        ax = origin_axis()
        rep = st.add(f"TESSELLATED_SHAPE_REPRESENTATION('{esc(product)} part {k}',(#{ax},#{shell}),#{ctx})")
        pd, pds = product_def(f"{product} part {k}")
        st.add(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{rep})")
        part[k] = (pd, rep, ax)

    # assembly nodes: the root, and a sub-assembly for each named group
    nodes = {}
    def node(name):
        pd, pds = product_def(name)
        rep = st.reserve()                        # written last, once its placements are known
        st.add(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{rep})")
        return {"pd": pd, "rep": rep, "name": name, "items": [origin_axis()]}
    root = node(product)
    nauo = [0]

    def occurrence(parent, child_pd, child_rep, child_axis, mat):
        R = mat
        loc = st.add(f"CARTESIAN_POINT('',({real(R[3])},{real(R[7])},{real(R[11])}))")
        z = st.add(f"DIRECTION('',({real(R[2])},{real(R[6])},{real(R[10])}))")
        x = st.add(f"DIRECTION('',({real(R[0])},{real(R[4])},{real(R[8])}))")
        ax = st.add(f"AXIS2_PLACEMENT_3D('',#{loc},#{z},#{x})")
        parent["items"].append(ax)
        nauo[0] += 1
        n = st.add(f"NEXT_ASSEMBLY_USAGE_OCCURRENCE('NAUO{nauo[0]}','','',#{parent['pd']},#{child_pd},$)")
        pds = st.add(f"PRODUCT_DEFINITION_SHAPE('','',#{n})")
        idt = st.add(f"ITEM_DEFINED_TRANSFORMATION('','',#{child_axis},#{ax})")
        rr = st.add(f"( REPRESENTATION_RELATIONSHIP('','',#{child_rep},#{parent['rep']}) "
                    f"REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#{idt}) SHAPE_REPRESENTATION_RELATIONSHIP() )")
        st.add(f"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#{rr},#{pds})")

    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0]
    for g in sorted({inst[2] if len(inst) > 2 else 0 for inst in m.instances}):
        if g in groups:
            nodes[g] = node(groups[g])
            occurrence(root, nodes[g]["pd"], nodes[g]["rep"], nodes[g]["items"][0], identity)
    for inst in m.instances:
        k, mat = inst[0], inst[1]
        g = inst[2] if len(inst) > 2 else 0
        if k not in part: continue
        pd, rep, ax = part[k]
        occurrence(nodes.get(g, root), pd, rep, ax, mat)
    for nd in [root] + list(nodes.values()):
        st.put(nd["rep"], "SHAPE_REPRESENTATION('%s',(%s),#%d)" % (esc(nd["name"]), ",".join(f"#{i}" for i in nd["items"]), ctx))
    st.add("MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',(%s),#%d)" % (",".join(f"#{s}" for s in styled), ctx))

    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    name = out_path.rsplit("/", 1)[-1]
    with open(out_path, "w") as f:
        f.write("ISO-10303-21;\nHEADER;\n")
        f.write(f"FILE_DESCRIPTION(('{esc(product)}, tessellated'),'2;1');\n")
        f.write(f"FILE_NAME('{esc(name)}','{stamp}',('Skylar Denno'),(''),'skylar.solutions bin-to-step','','');\n")
        f.write("FILE_SCHEMA(('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303 442 1 1 4 }'));\nENDSEC;\nDATA;\n")
        for i, e in enumerate(st.ents, 1):
            f.write(f"#{i}={e};\n")
        f.write("ENDSEC;\nEND-ISO-10303-21;\n")
    print(f"{out_path}: {len(part)} parts, {nauo[0]} placements, {len(styles)} colours, {len(st.ents)} entities")


if __name__ == "__main__":
    if len(sys.argv) < 4: sys.exit(__doc__)
    main(*sys.argv[1:])
