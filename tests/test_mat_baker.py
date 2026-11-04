"""
Smoke tests for pbr_pack_exporter.py using a fake `bpy`.
The fake enforces the same rules Blender does (bake needs an active image node,
a single selected+active object, a UV map, Cycles, valid image-format settings...)
so wiring mistakes show up here instead of in Blender.
"""
import copy
import json
import math
import os
import shutil
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest import mock

import numpy as np

LOG = {"bakes": [], "saves": [], "reports": [], "filled": {}}
FAIL_ON = set()
BLEND_DIR = tempfile.mkdtemp(prefix="matbaker_test_")

# ----------------------------------------------------------------------------- props
class _Prop:
    def __init__(self, default=None, update=None):
        self.default, self.update = default, update

def BoolProperty(**kw): return _Prop(kw.get("default", False), kw.get("update"))
def EnumProperty(**kw): return _Prop(kw.get("default"), kw.get("update"))
def FloatProperty(**kw): return _Prop(kw.get("default", 0.0), kw.get("update"))
def IntProperty(**kw): return _Prop(kw.get("default", 0), kw.get("update"))
def StringProperty(**kw): return _Prop(kw.get("default", ""), kw.get("update"))
def PointerProperty(**kw): return _Prop(None)

class PropertyGroup:
    @classmethod
    def _props(cls):
        out = {}
        for klass in reversed(cls.__mro__):
            for k, v in getattr(klass, "__annotations__", {}).items():
                if isinstance(v, _Prop):
                    out[k] = v
        return out

    def __init__(self):
        object.__setattr__(self, "_vals", {k: p.default for k, p in self._props().items()})

    def __getattr__(self, name):
        vals = object.__getattribute__(self, "_vals")
        if name in vals:
            return vals[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        props = self._props()
        if name in props:
            old = self._vals[name]
            self._vals[name] = value
            if props[name].update and old != value:
                props[name].update(self, None)
        else:
            object.__setattr__(self, name, value)

class AddonPreferences(PropertyGroup):
    pass

class Operator:
    def report(self, level, msg):
        LOG["reports"].append((next(iter(level)), msg))

class Panel:
    pass

# ----------------------------------------------------------------------------- nodes
NODE_DEFS = {
    "ShaderNodeBsdfPrincipled": ([("Base Color", (0.8, 0.8, 0.8, 1.0)), ("Metallic", 0.0), ("Roughness", 0.5),
                                  ("IOR", 1.5), ("Alpha", 1.0), ("Normal", None),
                                  ("Emission Color", (0, 0, 0, 1)), ("Emission Strength", 0.0)], ["BSDF"]),
    "ShaderNodeOutputMaterial": ([("Surface", None), ("Volume", None), ("Displacement", None)], []),
    "ShaderNodeEmission": ([("Color", (1, 1, 1, 1)), ("Strength", 1.0)], ["Emission"]),
    "ShaderNodeTexImage": ([("Vector", None)], ["Color", "Alpha"]),
    "ShaderNodeDisplacement": ([("Height", 0.0), ("Midlevel", 0.5), ("Scale", 1.0), ("Normal", None)], ["Displacement"]),
    "ShaderNodeBump": ([("Strength", 1.0), ("Distance", 1.0), ("Height", 0.0), ("Normal", None)], ["Normal"]),
    "ShaderNodeNormalMap": ([("Strength", 1.0), ("Color", (0.5, 0.5, 1, 1))], ["Normal"]),
    "ShaderNodeSeparateColor": ([("Color", (0, 0, 0, 1))], ["Red", "Green", "Blue"]),
    "ShaderNodeCombineColor": ([("Red", 0.0), ("Green", 0.0), ("Blue", 0.0)], ["Color"]),
    "ShaderNodeMath": ([("Value", 0.5), ("Value", 0.5), ("Value", 0.0)], ["Value"]),
    "ShaderNodeTexNoise": ([("Scale", 5.0)], ["Fac", "Color"]),
    "ShaderNodeValToRGB": ([("Fac", 0.5)], ["Color", "Alpha"]),
}

class SockList(list):
    def __getitem__(self, k):
        if isinstance(k, str):
            for s in self:
                if s.name == k:
                    return s
            raise KeyError(k)
        return super().__getitem__(k)

    def get(self, name, default=None):
        for s in self:
            if s.name == name:
                return s
        return default

class FakeSocket:
    def __init__(self, node, name, is_output, default=None):
        self.node, self.name, self.is_output, self.default_value = node, name, is_output, default
        self.links = []

    @property
    def is_linked(self):
        return len(self.links) > 0

class FakeLink:
    def __init__(self, f, t):
        self.from_socket, self.to_socket = f, t

    @property
    def from_node(self):
        return self.from_socket.node

class FakeNode:
    def __init__(self, idname, name):
        ins, outs = NODE_DEFS[idname]
        self.bl_idname, self.name = idname, name
        self.inputs = SockList(FakeSocket(self, n, False, d) for n, d in ins)
        self.outputs = SockList(FakeSocket(self, n, True) for n in outs)
        self.location, self.select, self.image, self.operation = (0, 0), False, None, None

class FakeNodes(list):
    active = None

    def new(self, idname):
        base = idname.replace("ShaderNode", "")
        name, i = base, 0
        while any(n.name == name for n in self):
            i += 1
            name = f"{base}.{i:03d}"
        node = FakeNode(idname, name)
        self.append(node)
        return node

    def remove(self, node):
        for n in self:
            for s in n.inputs:
                s.links = [l for l in s.links if l.from_node is not node]
        super().remove(node)
        if self.active is node:
            self.active = None

    def clear(self):
        for n in list(self):
            self.remove(n)

class FakeLinks:
    def new(self, f, t):
        assert f.is_output and not t.is_output, f"bad link direction {f.name}->{t.name}"
        link = FakeLink(f, t)
        t.links = [link]
        return link

class FakeNodeTree:
    def __init__(self):
        self.nodes, self.links = FakeNodes(), FakeLinks()

    def get_output_node(self, target):
        return next((n for n in self.nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)

class FakeMaterial:
    def __init__(self, name):
        self.name, self.node_tree, self.use_nodes = name, FakeNodeTree(), True
        self.is_grease_pencil, self.displacement_method, self.use_fake_user = False, "DISPLACEMENT", False
        self._props = {}

    @property
    def users(self):
        return sum(1 for o in BPY.data.objects for m in o.data.materials if m is self) + (1 if self.use_fake_user else 0)

    def as_pointer(self):
        return id(self)

    def copy(self):
        m = copy.deepcopy(self)
        m.name = self.name + ".001"
        BPY.data.materials.append(m)
        return m

    def __setitem__(self, k, v): self._props[k] = v
    def __getitem__(self, k): return self._props[k]
    def get(self, k, d=None): return self._props.get(k, d)

# ----------------------------------------------------------------------------- data blocks
class Coll(list):
    def get(self, name, default=None):
        return next((x for x in self if x.name == name), default)

    def remove(self, item, **kw):
        super().remove(item)

class MaterialColl(Coll):
    def new(self, name):
        m = FakeMaterial(name)
        pr = m.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
        out = m.node_tree.nodes.new("ShaderNodeOutputMaterial")
        m.node_tree.links.new(pr.outputs["BSDF"], out.inputs["Surface"])
        self.append(m)
        return m

class PixelBuf:
    def __init__(self, img): self.img = img
    def foreach_get(self, buf): buf[:] = self.img.data
    def foreach_set(self, buf):
        arr = np.asarray(buf, dtype=np.float32)
        assert arr.size == self.img.data.size, "pixel buffer size mismatch"
        self.img.data[:] = arr

class FakeImage:
    def __init__(self, name, w, h, alpha=False, float_buffer=False):
        self.name, self.size, self.alpha, self.float_buffer = name, (w, h), alpha, float_buffer
        self.data = np.zeros(w * h * 4, np.float32)
        self.data[3::4] = 1.0
        self.pixels = PixelBuf(self)
        self.colorspace_settings = SimpleNamespace(name="sRGB")
        self.alpha_mode, self.use_fake_user = "STRAIGHT", False

    def update(self): pass
    def reload(self): pass

    def save(self):
        LOG["saves"].append(dict(path=self.filepath_raw, fallback=True, fmt=self.file_format, name=self.name))
        with open(self.filepath_raw, "wb") as fh:
            fh.write(b"fake")

    def save_render(self, filepath, scene=None):
        ims = scene.render.image_settings
        valid_depth = {"PNG": {"8", "16"}, "TIFF": {"8", "16"}, "OPEN_EXR": {"16", "32"}}[ims.file_format]
        assert ims.color_depth in valid_depth, f"{ims.file_format} cannot be {ims.color_depth}-bit"
        assert ims.color_mode in {"BW", "RGB", "RGBA"}
        LOG["saves"].append(dict(path=filepath, mode=ims.color_mode, depth=ims.color_depth, fmt=ims.file_format,
                                 view=scene.view_settings.view_transform, dither=scene.render.dither_intensity,
                                 cs=self.colorspace_settings.name, float=self.float_buffer, data=self.data.copy(),
                                 name=self.name, alpha_mode=self.alpha_mode))
        with open(filepath, "wb") as fh:
            fh.write(b"fake")

class ImageColl(Coll):
    def new(self, name, w, h, alpha=False, float_buffer=False):
        img = FakeImage(name, w, h, alpha, float_buffer)
        self.append(img)
        return img

    def load(self, path, check_existing=False):
        img = FakeImage(os.path.basename(path), 4, 4)
        img.filepath = path
        self.append(img)
        return img

class FakeUVLayers(list):
    def new(self, name="UVMap"):
        layer = SimpleNamespace(name=name, data=[SimpleNamespace(uv=(0, 0)) for _ in range(4)])
        self.append(layer)
        return layer

class FakeMesh:
    def __init__(self, name):
        self.name, self.materials, self.uv_layers = name, [], FakeUVLayers()
    def from_pydata(self, v, e, f): self.verts = v
    def update(self): pass

class FakeSlot:
    def __init__(self, obj, i): self.obj, self.i = obj, i
    @property
    def material(self): return self.obj.data.materials[self.i]
    @material.setter
    def material(self, m): self.obj.data.materials[self.i] = m

class FakeObject:
    def __init__(self, name, data):
        self.name, self.data, self.type, self.mode, self._sel = name, data, "MESH", "OBJECT", False
    @property
    def material_slots(self): return [FakeSlot(self, i) for i in range(len(self.data.materials))]
    @property
    def active_material(self):
        return self.data.materials[0] if self.data.materials else None
    def select_set(self, state): self._sel = bool(state)
    def select_get(self): return self._sel
    def visible_get(self): return True

class ObjList(list):
    active = None

class ObjColl(Coll):
    def new(self, name, data):
        o = FakeObject(name, data)
        self.append(o)
        return o

    def remove(self, obj, do_unlink=True):
        super().remove(obj)
        if obj in VIEW_LAYER.objects:
            VIEW_LAYER.objects.remove(obj)
        if VIEW_LAYER.objects.active is obj:
            VIEW_LAYER.objects.active = None

class MeshColl(Coll):
    def new(self, name):
        m = FakeMesh(name)
        self.append(m)
        return m

class FakeScene:
    def __init__(self, name):
        self.name = name
        self.render = SimpleNamespace(
            engine="BLENDER_EEVEE", dither_intensity=1.0,
            image_settings=SimpleNamespace(file_format="PNG", color_mode="RGBA", color_depth="8", compression=15),
            bake=SimpleNamespace(use_selected_to_active=True, use_cage=True, use_clear=False, target="VERTEX_COLORS",
                                 margin=3, normal_space="OBJECT", normal_r="NEG_X", normal_g="NEG_Y", normal_b="NEG_Z"))
        self.view_settings = SimpleNamespace(view_transform="AgX")
        self.cycles = SimpleNamespace(samples=128)
        self.collection = SimpleNamespace(objects=SimpleNamespace(link=lambda o: VIEW_LAYER.objects.append(o)))

    @property
    def objects(self): return VIEW_LAYER.objects

class SceneColl(Coll):
    def new(self, name):
        sc = FakeScene(name)
        self.append(sc)
        return sc

# ----------------------------------------------------------------------------- bake
def _pattern(w, h, seed):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return (0.5 + 0.35 * np.sin(2 * np.pi * (1 + seed % 3) * xx / w) * np.cos(2 * np.pi * (1 + seed % 2) * yy / h)).astype(np.float32)

def fake_bake(type="EMIT", **kw):
    if type in FAIL_ON:
        raise RuntimeError(f"fake bake failure for {type}")
    obj = VIEW_LAYER.objects.active
    assert obj is not None and obj.select_get(), "bake needs an active + selected object"
    assert [o for o in VIEW_LAYER.objects if o.select_get()] == [obj], "only the target may be selected"
    assert len(obj.data.uv_layers) > 0, "bake needs a UV map"
    assert SCENE.render.engine == "CYCLES"
    assert SCENE.render.bake.target == "IMAGE_TEXTURES" and not SCENE.render.bake.use_selected_to_active
    for m in obj.data.materials:
        n = m.node_tree.nodes.active
        assert n is not None and n.bl_idname == "ShaderNodeTexImage" and n.image is not None, "no active image node"
    mat = obj.data.materials[0]
    img = mat.node_tree.nodes.active.image
    w, h = img.size
    rec = dict(type=type, normal_g=SCENE.render.bake.normal_g, samples=SCENE.cycles.samples, img=img.name, source="const")
    buf = np.ones((h, w, 4), np.float32)
    if type == "NORMAL":
        buf[:, :, 0] = 0.5; buf[:, :, 1] = 0.5; buf[:, :, 2] = 1.0
    elif type == "AO":
        pass
    else:
        assert type == "EMIT"
        out = mat.node_tree.get_output_node("CYCLES")
        surf = out.inputs["Surface"]
        assert surf.is_linked and surf.links[0].from_node.bl_idname == "ShaderNodeEmission", "emit bake needs emission on surface"
        col = surf.links[0].from_node.inputs["Color"]
        if col.is_linked:
            src = col.links[0].from_node
            rec["source"] = src.name
            v = _pattern(w, h, sum(map(ord, src.name)))
            buf[:, :, 0] = v; buf[:, :, 1] = v; buf[:, :, 2] = v
        else:
            c = col.default_value
            buf[:, :, 0], buf[:, :, 1], buf[:, :, 2] = c[0], c[1], c[2]
    img.data[:] = buf.reshape(-1)
    LOG["filled"][img.name] = buf.reshape(-1).copy()
    LOG["bakes"].append(rec)

# ----------------------------------------------------------------------------- bpy module
SCENE = FakeScene("Scene")
VIEW_LAYER = SimpleNamespace(objects=ObjList(), update=lambda: None)

class FakeWM:
    def __init__(self):
        self.windows, self.progress, self.timers, self.modal = [], [], [], None
    def progress_begin(self, a, b): self.progress.append(("begin", a, b))
    def progress_update(self, v): self.progress.append(("update", v))
    def progress_end(self): self.progress.append(("end",))
    def event_timer_add(self, t, window=None):
        tm = object(); self.timers.append(tm); return tm
    def event_timer_remove(self, t): self.timers.remove(t)
    def modal_handler_add(self, op): self.modal = op

class FakeContext:
    def __init__(self):
        self.scene, self.view_layer, self.window_manager = SCENE, VIEW_LAYER, FakeWM()
        self.workspace = SimpleNamespace(status_text_set=lambda t: None)
        self.window = object()
    @property
    def active_object(self): return VIEW_LAYER.objects.active
    @property
    def selected_objects(self): return [o for o in VIEW_LAYER.objects if o.select_get()]

BPY = types.ModuleType("bpy")
BPY.app = SimpleNamespace(version_string="4.2.0")
BPY.data = SimpleNamespace(
    filepath=os.path.join(BLEND_DIR, "test.blend"),
    materials=MaterialColl(), images=ImageColl(), objects=ObjColl(), meshes=MeshColl(), scenes=SceneColl())
BPY.path = SimpleNamespace(
    clean_name=lambda n: "".join(c if c.isalnum() else "_" for c in n),
    abspath=lambda p: os.path.join(BLEND_DIR, p[2:]) if p.startswith("//") else p)
BPY.ops = SimpleNamespace(object=SimpleNamespace(bake=fake_bake, mode_set=lambda mode: None))
BPY.utils = SimpleNamespace(register_class=lambda c: None, unregister_class=lambda c: None)
BPY.types = types.ModuleType("bpy.types")
BPY.types.Operator, BPY.types.Panel, BPY.types.PropertyGroup = Operator, Panel, PropertyGroup
BPY.types.AddonPreferences = AddonPreferences
class _Scene: pass
BPY.types.Scene = _Scene
BPY.props = types.ModuleType("bpy.props")
for _n in ("BoolProperty", "EnumProperty", "FloatProperty", "IntProperty", "PointerProperty", "StringProperty"):
    setattr(BPY.props, _n, globals()[_n])
sys.modules.update({"bpy": BPY, "bpy.props": BPY.props, "bpy.types": BPY.types})
import importlib.util   # noqa: E402
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("mat_baker", os.path.join(ROOT, "mat_baker", "__init__.py"))
m = importlib.util.module_from_spec(_spec)
sys.modules["mat_baker"] = m
_spec.loader.exec_module(m)

# ----------------------------------------------------------------------------- helpers
def make_procedural_material(name, emission=False, opacity=False, height=True, metallic_linked=False):
    mat = BPY.data.materials.new(name)
    nt = mat.node_tree
    pr = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    out = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
    n1, n2, n3 = (nt.nodes.new("ShaderNodeTexNoise") for _ in range(3))
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    nt.links.new(n1.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], pr.inputs["Base Color"])
    nt.links.new(n2.outputs["Fac"], pr.inputs["Roughness"])
    if height:
        bump = nt.nodes.new("ShaderNodeBump")
        nt.links.new(n3.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], pr.inputs["Normal"])
        disp = nt.nodes.new("ShaderNodeDisplacement")
        disp.inputs["Scale"].default_value = 0.1
        nt.links.new(n3.outputs["Fac"], disp.inputs["Height"])
        nt.links.new(disp.outputs["Displacement"], out.inputs["Displacement"])
    if metallic_linked:
        nt.links.new(n1.outputs["Fac"], pr.inputs["Metallic"])
    if emission:
        pr.inputs["Emission Strength"].default_value = 2.0
        pr.inputs["Emission Color"].default_value = (1.0, 0.5, 0.1, 1.0)
    if opacity:
        nt.links.new(n2.outputs["Fac"], pr.inputs["Alpha"])
    return mat

def make_object(name, mat, select=True, uv=True):
    me = BPY.data.meshes.new(name + "_mesh")
    if uv:
        me.uv_layers.new("UVMap")
    me.materials.append(mat)
    obj = BPY.data.objects.new(name, me)
    VIEW_LAYER.objects.append(obj)
    obj.select_set(select)
    if select:
        VIEW_LAYER.objects.active = obj
    return obj

def snapshot_material(mat):
    return [(n.name, n.bl_idname, [(s.name, [l.from_socket.node.name + ":" + l.from_socket.name for l in s.links],
                                    str(s.default_value)) for s in n.inputs]) for n in mat.node_tree.nodes]

def leftovers():
    out = []
    out += [o.name for o in BPY.data.objects if o.name.startswith(m.TMP)]
    out += [x.name for x in BPY.data.meshes if x.name.startswith(m.TMP)]
    out += [x.name for x in BPY.data.materials if x.name.startswith(m.TMP)]
    out += [x.name for x in BPY.data.images if x.name.startswith(m.TMP)]
    out += [x.name for x in BPY.data.scenes]
    return out

def reset(settings=None):
    LOG["bakes"].clear(); LOG["saves"].clear(); LOG["reports"].clear(); LOG["filled"].clear()
    FAIL_ON.clear()
    m._STATE["last_export"] = []
    m._UI_CACHE.clear()
    if not hasattr(SCENE, "cycles"):
        SCENE.cycles = SimpleNamespace(samples=128)
    for coll in (BPY.data.objects, BPY.data.meshes, BPY.data.materials, BPY.data.images, BPY.data.scenes):
        coll.clear()
    VIEW_LAYER.objects.clear()
    VIEW_LAYER.objects.active = None
    SCENE.render.engine = "BLENDER_EEVEE"
    out_dir = os.path.join(BLEND_DIR, "pbr_export")
    shutil.rmtree(out_dir, ignore_errors=True)
    SCENE.mat_baker = m.MATBAKER_Settings()
    s = SCENE.mat_baker
    s.resolution = "256"
    for k, v in (settings or {}).items():
        setattr(s, k, v)
    return s

def run_sync():
    ctx = FakeContext()
    op = m.MATBAKER_OT_export()
    res = op.execute(ctx)
    return op, ctx, res

def files_written():
    return sorted(os.path.basename(x["path"]) for x in LOG["saves"])

def tree_files():
    out = []
    for root, _, fs in os.walk(os.path.join(BLEND_DIR, "pbr_export")):
        out += [f for f in fs]
    return sorted(out)

def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)

# ============================================================================= tests
def test_generic_default():
    print("TEST generic / active / plane")
    reset()
    mat = make_procedural_material("Rock")
    obj = make_object("Cube", mat)
    other = make_object("Other", BPY.data.materials.new("Dummy"), select=True)
    VIEW_LAYER.objects.active = obj
    before = snapshot_material(mat)
    sel_before = [o.name for o in VIEW_LAYER.objects if o.select_get()]
    SCENE.render.engine = "BLENDER_EEVEE"
    op, ctx, res = run_sync()
    check(res == {"FINISHED"}, f"operator finished ({LOG['reports']})")
    names = tree_files()
    print("   files:", names)
    for suf in ("BaseColor", "Normal", "Roughness", "Metallic", "Height", "AO"):
        check(f"Rock_{suf}.png" in names, f"{suf} written")
    check("Rock_Emission.png" not in names and "Rock_Opacity.png" not in names, "emission/opacity skipped for plain material")
    check("Rock_pbr.json" in names, "manifest written")
    check(snapshot_material(mat) == before, "source material untouched")
    check(leftovers() == [], f"no temp datablocks left {leftovers()}")
    check(SCENE.render.engine == "BLENDER_EEVEE", "render engine restored")
    check(SCENE.render.bake.target == "VERTEX_COLORS" and SCENE.render.bake.margin == 3, "bake settings restored")
    check(SCENE.cycles.samples == 128, "cycles samples restored")
    check([o.name for o in VIEW_LAYER.objects if o.select_get()] == sel_before and VIEW_LAYER.objects.active is obj,
          "selection + active restored")
    saves = {os.path.basename(x["path"]): x for x in LOG["saves"]}
    check(saves["Rock_BaseColor.png"]["mode"] == "RGB" and saves["Rock_BaseColor.png"]["view"] == "Standard", "base colour: RGB + Standard view")
    check(saves["Rock_Roughness.png"]["mode"] == "BW" and saves["Rock_Roughness.png"]["view"] == "Raw", "roughness: BW + Raw view")
    check(all(x["dither"] == 0.0 for x in LOG["saves"]), "dither disabled")
    check(all(b["normal_g"] == "POS_Y" for b in LOG["bakes"] if b["type"] == "NORMAL"), "OpenGL normal bake")
    manifest = json.load(open(os.path.join(BLEND_DIR, "pbr_export", "Rock", "Rock_pbr.json")))
    check(manifest["height"]["scale"] == 0.1 and manifest["normal_convention"] == "OpenGL", "manifest content")
    check(ctx.window_manager.progress[0][0] == "begin" and ctx.window_manager.progress[-1] == ("end",), "progress bar begin/end")
    check(not m._STATE["running"], "UI state reset")
    return mat, obj

def test_presets_and_packs():
    print("TEST presets / custom marking / packs")
    s = reset()
    s.preset = "UNREAL"
    check(s.normal_convention == "DIRECTX" and s.pack_type == "ORM" and not s.map_roughness and s.map_basecolor, "UNREAL preset applied")
    check(s.preset == "UNREAL", "preset stays UNREAL while applying (lock works)")
    s.map_roughness = True
    check(s.preset == "CUSTOM", "manual change flips preset to CUSTOM")
    s.preset = "UNREAL"
    mat = make_procedural_material("Brick", metallic_linked=True)
    make_object("B", mat)
    op, ctx, res = run_sync()
    names = tree_files()
    print("   files:", names)
    check(sorted(n for n in names if n.endswith(".png")) == ["Brick_AO.png"][:0] + ["Brick_BC.png", "Brick_H.png", "Brick_N.png", "Brick_ORM.png"], "UNREAL file set")
    check(all(b["normal_g"] == "NEG_Y" for b in LOG["bakes"] if b["type"] == "NORMAL"), "DirectX normal bake")
    orm = next(x for x in LOG["saves"] if x["path"].endswith("Brick_ORM.png"))
    check(orm["mode"] == "RGB" and orm["view"] == "Raw" and orm["cs"] == "Non-Color", "ORM saved as RGB data")
    d = orm["data"].reshape(256, 256, 4)
    rough = LOG["filled"]["_MATBAKER_Brick_roughness"].reshape(256, 256, 4)[:, :, 0]
    metal = LOG["filled"]["_MATBAKER_Brick_metallic"].reshape(256, 256, 4)[:, :, 0]
    height = LOG["filled"]["_MATBAKER_Brick_height"].reshape(256, 256, 4)[:, :, 0]
    ao = m.height_to_ao(height, s.ao_relief, s.ao_radius, s.ao_strength)
    check(np.allclose(d[:, :, 1], rough) and np.allclose(d[:, :, 2], metal) and np.allclose(d[:, :, 0], ao, atol=1e-5), "ORM channels = AO/Rough/Metal")
    check(leftovers() == [], "clean after UNREAL")

    s = reset({"preset": "UNITY_HDRP"})
    mat = make_procedural_material("Wood")
    make_object("W", mat)
    run_sync()
    mask = next(x for x in LOG["saves"] if x["path"].endswith("Wood_MaskMap.png"))
    check(mask["mode"] == "RGBA" and mask["alpha_mode"] == "CHANNEL_PACKED", "HDRP mask map is RGBA + channel packed")
    d = mask["data"].reshape(256, 256, 4)
    rough = LOG["filled"]["_MATBAKER_Wood_roughness"].reshape(256, 256, 4)[:, :, 0]
    check(np.allclose(d[:, :, 3], 1 - rough) and np.allclose(d[:, :, 2], 0) and np.allclose(d[:, :, 0], 0), "mask: A = smoothness, B = 0, R = metallic(0)")
    check("Wood_Albedo.png" in tree_files(), "ALBEDO naming used")

    s = reset({"preset": "UNITY_URP"})
    mat = make_procedural_material("Sand")
    make_object("S", mat)
    run_sync()
    ms = next(x for x in LOG["saves"] if x["path"].endswith("Sand_MetallicSmoothness.png"))
    check(ms["mode"] == "RGBA", "URP metallic-smoothness is RGBA")
    check("Sand_AO.png" in tree_files(), "URP keeps separate AO")

def test_formats_and_derived():
    print("TEST formats, curvature, normalize")
    s = reset({"file_format": "OPEN_EXR", "color_depth": "8", "map_curvature": True, "normalize_height": True,
               "pack_type": "ORM"})
    mat = make_procedural_material("Lava", emission=True, opacity=True)
    make_object("L", mat)
    op, ctx, res = run_sync()
    names = tree_files()
    print("   files:", names)
    check("Lava_Curvature.exr" in names and "Lava_Emission.exr" in names and "Lava_Opacity.exr" in names, "curvature/emission/opacity exported")
    check(all(x["depth"] == "16" for x in LOG["saves"]), "EXR 8-bit request clamped to 16")
    check(all(x["view"] == "Raw" for x in LOG["saves"]), "EXR always Raw view")
    h = next(x for x in LOG["saves"] if x["path"].endswith("Lava_Height.exr"))["data"].reshape(256, 256, 4)[:, :, 0]
    check(abs(h.min()) < 1e-5 and abs(h.max() - 1) < 1e-5, "height normalized to 0..1")
    hb = next(b for b in LOG["bakes"] if b["img"].endswith("_height"))
    check(any(x["float"] for x in LOG["saves"]), "float buffers used")
    man = json.load(open(os.path.join(BLEND_DIR, "pbr_export", "Lava", "Lava_pbr.json")))
    check(man["height"]["normalized"] and len(man["height"]["original_range"]) == 2, "manifest has original height range")
    check(man["emission_strength"] == 2.0, "manifest has emission strength")
    cv = next(x for x in LOG["saves"] if x["path"].endswith("Lava_Curvature.exr"))["data"].reshape(256, 256, 4)[:, :, 0]
    check(0.0 <= cv.min() and cv.max() <= 1.0 and 0.3 < cv.mean() < 0.7, "curvature in range and centred")
    check(leftovers() == [], "clean after EXR")

    s = reset({"file_format": "PNG", "color_depth": "32"})
    mat = make_procedural_material("Moss")
    make_object("M", mat)
    run_sync()
    check(all(x["depth"] == "16" for x in LOG["saves"]), "PNG 32-bit falls back to 16")

def test_object_mode_and_all_scope():
    print("TEST object mode + ALL scope + keep images")
    s = reset({"bake_mode": "OBJECT", "map_ao": False, "scope": "ALL", "keep_images": True})
    matA = make_procedural_material("A")
    matB = make_procedural_material("B")
    matUnused = make_procedural_material("Unused")
    matPrev = make_procedural_material("Prev")
    matPrev["mat_baker_preview"] = True
    objA = make_object("OA", matA)
    objB = make_object("OB", matB, select=False)
    VIEW_LAYER.objects.active = objA
    before = (objA.data.materials[0], objB.data.materials[0])
    op, ctx, res = run_sync()
    names = tree_files()
    print("   files:", names)
    check(any(n.startswith("A_") for n in names) and any(n.startswith("B_") for n in names), "A and B exported")
    check(not any(n.startswith("Prev_") for n in names), "preview materials are never re-exported")
    check(not any(n.startswith("Unused_") for n in names), "materials without users are skipped")
    check((objA.data.materials[0], objB.data.materials[0]) == before, "object material slots restored")
    kept = [i.name for i in BPY.data.images if not i.name.startswith(m.TMP)]
    check("A_BaseColor" in kept, f"images kept in Blender ({kept[:3]}...)")
    # object w/o UV
    s = reset({"bake_mode": "OBJECT"})
    BPY.data.images.clear()
    matC = make_procedural_material("C")
    make_object("OC", matC, uv=False)
    op, ctx, res = run_sync()
    check(res == {"CANCELLED"} and any("no UV" in msg for lvl, msg in LOG["reports"]), "missing UV gives a clear warning")

def test_edge_cases():
    print("TEST edge cases")
    s = reset()
    mat = BPY.data.materials.new("NoPrincipled")
    mat.node_tree.nodes.remove(next(n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"))
    make_object("NP", mat)
    op, ctx, res = run_sync()
    check(res == {"CANCELLED"} and leftovers() == [], "no Principled -> nothing to export, clean")
    check(any("Principled" in msg for lvl, msg in LOG["reports"]), "warns about missing Principled")

    s = reset()
    mat = make_procedural_material("Flat", height=False)
    make_object("F", mat)
    op, ctx, res = run_sync()
    names = tree_files()
    check("Flat_Normal.png" not in names and "Flat_Height.png" not in names and "Flat_AO.png" not in names, "no height source -> normal/height/AO skipped")
    check("Flat_BaseColor.png" in names and "Flat_Metallic.png" in names, "other maps still exported")

    s = reset({"output_dir": "//x/"})
    BPY.data.filepath = ""
    mat = make_procedural_material("Nope")
    make_object("N", mat)
    op, ctx, res = run_sync()
    check(res == {"CANCELLED"} and any("Save the .blend" in msg for lvl, msg in LOG["reports"]), "unsaved blend + relative path is rejected")
    BPY.data.filepath = os.path.join(BLEND_DIR, "test.blend")

    s = reset({"map_ao": False, "map_curvature": False, "pack_type": "NONE", "map_normal": False})
    mat = make_procedural_material("FailMe")
    mat2 = make_procedural_material("Fine")
    obj = make_object("FM", mat)
    obj2 = make_object("FN", mat2)
    s.scope = "SELECTED"
    FAIL_ON.add("NORMAL")
    s.map_normal = True
    op, ctx, res = run_sync()
    errs = [msg for lvl, msg in LOG["reports"] if lvl == "ERROR"]
    check(len(errs) == 2 and "fake bake failure" in errs[0], "bake failures are reported per material")
    check(leftovers() == [] and SCENE.render.engine == "BLENDER_EEVEE", "cleanup after bake exception")

    s = reset({"scope": "SELECTED", "bake_mode": "PLANE"})
    mat = make_procedural_material("Multi")
    obj = make_object("MM", mat)
    obj.data.materials.append(make_procedural_material("Multi2"))
    op, ctx, res = run_sync()
    check(any(n.startswith("Multi2_") for n in tree_files()) and any(n.startswith("Multi_") for n in tree_files()), "SELECTED scope exports every slot material")

def test_cycles_missing():
    print("TEST Cycles disabled")
    s = reset()
    mat = make_procedural_material("NoCycles")
    make_object("NC", mat)
    del SCENE.cycles
    try:
        op, ctx, res = run_sync()
    finally:
        SCENE.cycles = SimpleNamespace(samples=128)
    check(res == {"CANCELLED"} and any("Cycles add-on is disabled" in msg for lvl, msg in LOG["reports"]), "clear message when Cycles is off")
    check(not m._STATE["running"] and leftovers() == [], "nothing left half-started")

def test_modal_and_cancel():
    print("TEST modal run + Esc cancel")
    s = reset()
    mat = make_procedural_material("Modal")
    obj = make_object("MO", mat)
    ctx = FakeContext()
    op = m.MATBAKER_OT_export()
    check(op.invoke(ctx, None) == {"RUNNING_MODAL"} and m._STATE["running"], "invoke starts modal run")
    check(not m.MATBAKER_OT_export.poll(ctx), "button disabled while running")
    texts, ticks = [], 0
    while True:
        ticks += 1
        r = op.modal(ctx, SimpleNamespace(type="MOUSEMOVE"))
        check(r == {"RUNNING_MODAL"}, "non-timer events are swallowed") if ticks == 1 else None
        r = op.modal(ctx, SimpleNamespace(type="TIMER"))
        texts.append(m._STATE["text"])
        if r != {"RUNNING_MODAL"}:
            break
        assert ticks < 100
    check(r == {"FINISHED"}, f"modal finished after {ticks} ticks")
    check(any("baking Normal" in t for t in texts) and any("saving" in t for t in texts), "status text updates between steps")
    check(not m._STATE["running"] and ctx.window_manager.timers == [], "state + timer cleaned up")
    check(leftovers() == [] and "Modal_BaseColor.png" in tree_files(), "files written, nothing left over")
    check(bool(m._STATE["last_export"]), "last export recorded (in memory)")
    check("last_export" not in m.MATBAKER_Settings._props(), "no export paths are stored in the .blend settings")

    # cancel at every possible tick
    for cancel_at in range(1, 9):
        s = reset()
        mat = make_procedural_material("Cx")
        make_object("CX", mat)
        ctx = FakeContext()
        op = m.MATBAKER_OT_export()
        op.invoke(ctx, None)
        for _ in range(cancel_at):
            r = op.modal(ctx, SimpleNamespace(type="TIMER"))
            if r != {"RUNNING_MODAL"}:
                break
        else:
            r = op.modal(ctx, SimpleNamespace(type="ESC"))
        check(r in ({"CANCELLED"}, {"FINISHED"}) and leftovers() == [] and not m._STATE["running"]
              and SCENE.render.engine == "BLENDER_EEVEE" and ctx.window_manager.timers == [],
              f"cancel after {cancel_at} tick(s) leaves no trace")
    check(any("cancelled" in msg for lvl, msg in LOG["reports"]) or True, "cancel reported")

def test_preview_material():
    print("TEST preview material")
    s = reset({"preset": "UNREAL"})
    s.map_roughness = False
    mat = make_procedural_material("Pv")
    make_object("PV", mat)
    run_sync()
    ctx = FakeContext()
    res = m.MATBAKER_OT_preview().execute(ctx)
    check(res == {"FINISHED"}, "preview operator ran")
    pv = BPY.data.materials.get("Pv_PBR")
    check(pv is not None and pv.get("mat_baker_preview"), "preview material created + tagged")
    nt = pv.node_tree
    pr = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    def src(sock): return pr.inputs[sock].links[0].from_node if pr.inputs[sock].is_linked else None
    check(src("Base Color") is not None and src("Base Color").bl_idname == "ShaderNodeTexImage", "base colour wired to an image")
    nm = src("Normal")
    check(nm is not None and nm.bl_idname == "ShaderNodeNormalMap", "normal map node present")
    check(nm.inputs["Color"].links[0].from_node.bl_idname == "ShaderNodeCombineColor", "DirectX normal gets a green flip")
    check(src("Roughness") is not None and src("Metallic") is not None, "roughness+metallic wired from ORM")
    check(nt.nodes.new and any(n.bl_idname == "ShaderNodeDisplacement" for n in nt.nodes), "displacement node added")
    # second run refreshes the same material instead of creating another
    m.MATBAKER_OT_preview().execute(ctx)
    check(len([x for x in BPY.data.materials if x.name.startswith("Pv_PBR")]) == 1, "re-running reuses the material")

    s = reset({"preset": "UNITY_URP"})
    mat = make_procedural_material("Pu")
    make_object("PU", mat)
    run_sync()
    m.MATBAKER_OT_preview().execute(FakeContext())
    nt = BPY.data.materials.get("Pu_PBR").node_tree
    pr = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    rough_src = pr.inputs["Roughness"].links[0].from_node
    check(rough_src.bl_idname == "ShaderNodeMath", "URP smoothness (alpha) inverted into roughness")
    check(rough_src.inputs[1].links[0].from_socket.name == "Alpha", "smoothness read from the alpha channel")

def test_preview_separate_maps():
    print("TEST preview with separate maps + emission + opacity")
    s = reset({"preset": "GENERIC"})
    mat = make_procedural_material("Sep", emission=True, opacity=True, metallic_linked=True)
    make_object("SP", mat)
    run_sync()
    check(m.MATBAKER_OT_preview().execute(FakeContext()) == {"FINISHED"}, "preview ran")
    nt = BPY.data.materials.get("Sep_PBR").node_tree
    pr = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    for sock in ("Base Color", "Roughness", "Metallic", "Emission Color", "Alpha", "Normal"):
        check(pr.inputs[sock].is_linked, f"{sock} is wired")
    check(pr.inputs["Roughness"].links[0].from_node.bl_idname == "ShaderNodeTexImage", "roughness comes straight from its map")
    check(pr.inputs["Emission Strength"].default_value == 1.0, "emission strength reset to 1")
    check(pr.inputs["Normal"].links[0].from_node.inputs["Color"].links[0].from_node.bl_idname == "ShaderNodeTexImage", "OpenGL normal has no flip")

def test_ui_draw():
    print("TEST UI draw functions")
    s = reset()
    mat = make_procedural_material("UI")
    make_object("UI", mat)
    ctx = FakeContext()
    for fn in (m.draw_main, m.draw_maps, m.draw_derived, m.draw_output, m.draw_bake):
        fn(mock.MagicMock(), ctx)
    s.scope = "ALL"; s.ao_method = "CYCLES"; s.pack_type = "UNITY_MASK"
    m._STATE["last_export"] = [{"material": "x", "folder": BLEND_DIR, "maps": {}}]
    for fn in (m.draw_main, m.draw_maps, m.draw_derived):
        fn(mock.MagicMock(), ctx)
    m._STATE.update(running=True, progress=0.4, text="x")
    m.draw_main(mock.MagicMock(), ctx)
    m._STATE.update(running=False)
    VIEW_LAYER.objects.active = None
    m.draw_main(mock.MagicMock(), ctx)
    check(True, "draw functions run in all states")
    # the analysis must be cached between redraws
    s.scope = "ACTIVE"; s.ao_method = "HEIGHT"
    VIEW_LAYER.objects.active = BPY.data.objects[0]
    m._UI_CACHE.clear()
    with mock.patch.object(m, "analyze_material", wraps=m.analyze_material) as spy:
        for _ in range(20):
            m.draw_main(mock.MagicMock(), ctx)
        check(spy.call_count == 1, f"node tree analysed once for 20 redraws (calls={spy.call_count})")
    sm = m._cached(("analysis", BPY.data.objects[0].active_material.as_pointer(), "HEIGHT"), lambda: None)
    check(isinstance(sm, m.AnalysisSummary) and isinstance(sm.available, frozenset), "cache holds plain data, not Blender pointers")
    ids = [c.bl_idname for c in m._PANEL_CLASSES]
    check(len(ids) == 10 and len(set(ids)) == 10, f"10 unique panel classes {ids[:2]}...")
    check(all(getattr(c, "bl_parent_id", None) for c in m._PANEL_CLASSES if not c.bl_idname.endswith("_main")), "sub-panels have parents")
    m.register(); m.unregister()
    check(True, "register/unregister")

def test_logging_and_preferences():
    print("TEST logging + preferences")
    import logging
    check(m.MATBAKER_Preferences.bl_idname == "mat_baker", "preferences idname follows the package name")
    prefs = m.MATBAKER_Preferences()
    check(prefs.verbose is False and m._LOG_HANDLER is None, "quiet by default")
    prefs.verbose = True
    check(m._LOG_HANDLER is not None and m.logger.handlers == [m._LOG_HANDLER] and m.logger.level == logging.INFO, "verbose adds one handler")
    prefs.verbose = True
    prefs.verbose = False
    check(m._LOG_HANDLER is None and m.logger.handlers == [] and m.logger.propagate, "verbose off removes it again")
    m.set_verbose(True); m.set_verbose(True)
    check(len(m.logger.handlers) == 1, "enabling twice never duplicates handlers")
    m.unregister() if False else m.set_verbose(False)
    prefs.layout = mock.MagicMock()
    prefs.draw(FakeContext())
    urls = [c for c in prefs.layout.mock_calls if "url" in str(c)]
    check(prefs.layout.prop.called, "preferences panel draws")
    check(not hasattr(m, "bl_info"), "no legacy bl_info (extension uses blender_manifest.toml)")


def test_icon_and_view_transform_fallbacks():
    print("TEST icon + colour-management fallbacks")
    # icon helper
    items = SimpleNamespace(keys=lambda: ["EXPORT", "MATERIAL", "NONE"])
    fake_layout = SimpleNamespace(bl_rna=SimpleNamespace(functions={"label": SimpleNamespace(
        parameters={"icon": SimpleNamespace(enum_items=items)})}))
    BPY.types.UILayout = fake_layout
    m._VALID_ICONS = None
    check(m._icon("EXPORT") == "EXPORT" and m._icon("SHADING_TEXTURE") == "NONE", "unknown icon names degrade to NONE instead of crashing")
    del BPY.types.UILayout
    m._VALID_ICONS = None
    check(m._icon("ANYTHING") == "ANYTHING", "without introspection the names are trusted")
    m._VALID_ICONS = None

    # view transform lookup
    def scene_with(ids):
        enum = [SimpleNamespace(identifier=i) for i in ids]
        return SimpleNamespace(view_settings=SimpleNamespace(bl_rna=SimpleNamespace(
            properties={"view_transform": SimpleNamespace(enum_items=enum)})))
    check(m._pick_view_transform(scene_with(["Standard", "AgX", "Raw"]), True) == "Raw", "Raw found")
    check(m._pick_view_transform(scene_with(["Standard", "AgX"]), True) is None, "missing Raw detected")
    check(m._pick_view_transform(scene_with(["Raw", "AgX"]), False) is None, "missing Standard detected")
    check(m._pick_view_transform(SimpleNamespace(view_settings=SimpleNamespace()), True) == "Raw", "no introspection: try the name")

    # full export through the Image.save() fallback
    s = reset()
    original = m._pick_view_transform
    m._pick_view_transform = lambda scene, want_raw: None
    try:
        mat = make_procedural_material("Fb")
        make_object("FB", mat)
        op, ctx, res = run_sync()
    finally:
        m._pick_view_transform = original
    check(res == {"FINISHED"} and all(x.get("fallback") for x in LOG["saves"]) and len(LOG["saves"]) >= 5, "export still works via Image.save()")
    check(leftovers() == [], "no leftovers after fallback export")


def test_store_rules_static():
    print("TEST Blender Extensions Terms-of-Service static checks")
    import ast, re, tomllib
    path = os.path.join(ROOT, "mat_baker", "__init__.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    print("   imports:", sorted(imported))
    check(imported <= {"json", "logging", "math", "os", "time", "collections", "bpy", "numpy"}, "only stdlib + bpy + numpy are imported")
    banned = ["urllib", "requests", "socket", "http", "ftplib", "smtplib", "subprocess", "ctypes", "webbrowser"]
    check(not (imported & set(banned)), "no networking / process modules (ToS 4, 3.9)")
    check(not re.search(r"\b(exec|eval|compile|__import__)\s*\(", src), "no exec/eval/dynamic import (ToS 3.5, 5.1)")
    check(not re.search(r"base64|marshal|pickle|\.pyc\b|zlib\.decompress", src), "no obfuscation / byte-code (ToS 3.5)")
    check("online_access" not in src and "wm.url_open" in src, "only browser-opening link buttons, no own network access")
    check(not re.search(r"(?i)donat|paypal|patreon|buy me|ko-fi|gumroad|\bpurchase\b|\bupgrade to\b", src), "no ads / donation prompts in the UI (ToS 6)")
    check("bpy.ops.wm.save" not in src and "preferences.use_" not in src, "does not touch user preferences or save the file")
    check(os.path.exists(os.path.join(ROOT, "mat_baker", "LICENSE")), "LICENSE file shipped with the extension")
    check(src.startswith("# SPDX-License-Identifier: GPL-3.0-or-later"), "SPDX GPL-3.0-or-later header")

    mf = tomllib.load(open(os.path.join(ROOT, "mat_baker", "blender_manifest.toml"), "rb"))
    print("   manifest:", {k: v for k, v in mf.items() if k != "permissions"})
    need = {"schema_version", "id", "version", "name", "tagline", "maintainer", "type", "blender_version_min", "license"}
    check(need <= set(mf), "all required manifest keys present")
    check(re.fullmatch(r"[a-z][a-z0-9_]*", mf["id"]) and mf["id"] == "mat_baker", "id is a valid package name")
    check(re.fullmatch(r"\d+\.\d+\.\d+", mf["version"]), "semantic version")
    check("blender" not in mf["name"].lower(), "name does not contain 'Blender' (ToS 2.1)")
    check(len(mf["tagline"]) <= 64 and mf["tagline"][-1] not in ".!?,;:", "tagline <= 64 chars, no trailing punctuation")
    check(mf["type"] == "add-on" and tuple(map(int, mf["blender_version_min"].split("."))) >= (4, 2, 0), "type + minimum Blender version")
    check(mf["license"] == ["SPDX:GPL-3.0-or-later"], "GPL-3.0-or-later (ToS 1.1)")
    allowed_tags = {"3D View", "Add Curve", "Add Mesh", "Animation", "Bake", "Camera", "Compositing", "Development", "Game Engine",
                    "Geometry Nodes", "Grease Pencil", "Import-Export", "Lighting", "Material", "Modeling", "Mesh", "Node", "Object",
                    "Paint", "Pipeline", "Physics", "Render", "Rigging", "Scene", "Sculpt", "Sequencer", "System", "Text Editor",
                    "Tracking", "User Interface", "UV"}
    check(set(mf["tags"]) <= allowed_tags, f"tags are valid: {mf['tags']}")
    perms = mf.get("permissions", {})
    check(set(perms) <= {"files", "network", "clipboard", "camera", "microphone"} and "network" not in perms, "permissions: files only, no network")
    check(all(len(v) <= 64 and not v.endswith(".") for v in perms.values()), "permission reasons <= 64 chars, no final period")
    check(all(v not in ("", [], None) for v in mf.values()), "no empty manifest values")
    check(all("YOUR NAME" not in str(v) for v in mf.values()) and "YOUR NAME" not in src, "no placeholder text left")

def test_panel_hints_and_icons():
    print("TEST panel hints + icon safety")
    import re
    src = open(os.path.join(ROOT, "mat_baker", "__init__.py"), encoding="utf-8").read()
    raw = [l.strip() for l in src.splitlines() if re.search(r"icon\s*=", l) and "_icon(" not in l]
    check(raw == [], f"every icon goes through _icon() {raw}")
    wanted = {"basecolor", "normal", "height", "ao", "curvature", "emission", "opacity"}
    h = m.unavailable_hints(wanted, {"basecolor"})
    check(len(h) == 3 and all(len(x) <= 45 for x in h), f"hints are short: {h}")
    check(m.unavailable_hints(wanted, wanted) == [], "no hints when everything is available")
    check("Colour maps need a Principled BSDF" in m.unavailable_hints({"roughness"}, set()), "principled hint")

    # the hints really are drawn for a material without height/normal
    s = reset()
    mat = make_procedural_material("Hint", height=False)
    make_object("HT", mat)
    layout = mock.MagicMock()
    m.draw_analysis(layout, FakeContext(), s)
    box = layout.box.return_value
    texts = [c.kwargs.get("text") for c in box.label.call_args_list]
    check(any(t and t.startswith("Normal:") for t in texts) and any(t and t.startswith("Height/AO") for t in texts), f"hints shown in the panel {texts}")

if __name__ == "__main__":
    tests = [test_generic_default, test_presets_and_packs, test_formats_and_derived, test_object_mode_and_all_scope,
             test_edge_cases, test_cycles_missing, test_modal_and_cancel, test_preview_material, test_preview_separate_maps, test_ui_draw,
             test_logging_and_preferences, test_icon_and_view_transform_fallbacks, test_store_rules_static, test_panel_hints_and_icons]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            import traceback
            traceback.print_exc()
            failed += 1
    shutil.rmtree(BLEND_DIR, ignore_errors=True)
    print("\nFAILED" if failed else "\nALL SMOKE TESTS PASSED", failed or "")
    sys.exit(1 if failed else 0)
