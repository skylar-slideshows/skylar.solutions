"""Minimal KiCad S-expression reader (kicad_pcb / kicad_sch)."""
import re
TOK = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+')
def parse(text):
    stack, cur = [], []
    for m in TOK.finditer(text):
        t = m.group(0)
        if t == '(':
            stack.append(cur); cur = []
        elif t == ')':
            done = cur; cur = stack.pop(); cur.append(done)
        elif t[0] == '"':
            cur.append(t[1:-1].replace('\\"', '"').replace('\\\\', '\\'))
        else:
            cur.append(t)
    return cur[0]
def kids(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]
def kid(node, name):
    for c in node:
        if isinstance(c, list) and c and c[0] == name: return c
    return None
def val(node, name, i=1, default=None):
    k = kid(node, name)
    return k[i] if k and len(k) > i else default
def prop(fp, key):
    for p in kids(fp, 'property'):
        if len(p) > 2 and p[1] == key: return p[2]
    for p in kids(fp, 'fp_text'):  # KiCad 6 style
        if len(p) > 2 and p[1] == key.lower(): return p[2]
    return None
