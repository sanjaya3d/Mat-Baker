# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 SANJAYA3D
"""
Mat-Baker  (v1.0.0)
===================
Bake procedural materials into PBR texture packs.   Author: SANJAYA3D

Bakes a node-based (procedural) material into a set of PBR texture maps.

Easy mode : pick an engine preset, pick a resolution, press Export.
Advanced  : every knob (maps, packing, AO/curvature, format, bake settings).

Maps        Base Color, Normal, Roughness, Metallic, Height, Ambient Occlusion,
            Emission, Opacity, Curvature
Packed maps ORM (glTF / Unreal / Godot), Unity HDRP Mask map,
            Unity Metallic-Smoothness (URP / Built-in)
Presets     Generic, Unreal, Unity URP, Unity HDRP, glTF/Godot/Three.js, Everything

How it works
------------
* Works on a COPY of the material - your original is never touched.
* Base Color / Roughness / Metallic / Emission / Alpha / Height are baked by
  routing the node feeding that Principled BSDF input through an Emission
  shader (EMIT bake). Works for any procedural network.
* Normal is a tangent-space Cycles bake (bump/displacement become normals).
* AO / Curvature are computed from the height map (ideal for flat tileable
  textures) - AO can alternatively be baked by Cycles on real geometry.
* The export runs as a modal operator: the UI stays alive, progress is shown
  in the panel and Esc cancels (after the current map).

Bake targets
------------
* Flat Plane (default): temporary plane with 0-1 UVs -> tileable packs.
* Object's UVs: bakes into the object's own UV map.

Requirements: Blender 4.2+, Cycles enabled, Principled BSDF at the top level
of the material's node tree.
"""

import json
import logging
import math
import os
import time
from collections import deque

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup

try:
    import numpy as np
except ImportError:  # numpy ships with Blender; stay defensive anyway
    np = None

logger = logging.getLogger(__name__)

# Project links (opened in the browser only when the user presses the buttons in the add-on preferences).
URL_DOCS = "https://github.com/SANJAYA3D/Mat-Baker#readme"
URL_ISSUES = "https://github.com/SANJAYA3D/Mat-Baker/issues"


TMP = "_MATBAKER_"

MAP_ORDER = ("basecolor", "normal", "roughness", "metallic", "height", "ao", "emission", "opacity", "curvature")

# 'data' maps are stored as Non-Color; 'bw' maps are saved as single-channel files.
MAP_INFO = {
    "basecolor": {"label": "Base Color", "sockets": ("Base Color",), "data": False, "bw": False},
    "normal": {"label": "Normal", "sockets": (), "data": True, "bw": False},
    "roughness": {"label": "Roughness", "sockets": ("Roughness",), "data": True, "bw": True},
    "metallic": {"label": "Metallic", "sockets": ("Metallic",), "data": True, "bw": True},
    "height": {"label": "Height", "sockets": (), "data": True, "bw": True},
    "ao": {"label": "Ambient Occlusion", "sockets": (), "data": True, "bw": True},
    # 4.x calls it "Emission Color", 3.x just "Emission"
    "emission": {"label": "Emission", "sockets": ("Emission Color", "Emission"), "data": False, "bw": False},
    "opacity": {"label": "Opacity", "sockets": ("Alpha",), "data": True, "bw": True},
    "curvature": {"label": "Curvature", "sockets": (), "data": True, "bw": True},
}

SUFFIXES = {
    "GENERIC": {
        "basecolor": "BaseColor", "normal": "Normal", "roughness": "Roughness",
        "metallic": "Metallic", "height": "Height", "ao": "AO",
        "emission": "Emission", "opacity": "Opacity", "curvature": "Curvature",
    },
    "ALBEDO": {
        "basecolor": "Albedo", "normal": "Normal", "roughness": "Roughness",
        "metallic": "Metallic", "height": "Height", "ao": "AO",
        "emission": "Emission", "opacity": "Opacity", "curvature": "Curvature",
    },
    "UNREAL": {
        "basecolor": "BC", "normal": "N", "roughness": "R",
        "metallic": "M", "height": "H", "ao": "AO",
        "emission": "E", "opacity": "O", "curvature": "C",
    },
}

# Channel sources: a map key, "smoothness" (= 1 - roughness), a constant, or None (leave default).
PACKS = {
    "ORM": {
        "name": "ORM", "suffix": "ORM", "alpha": False,
        "channels": ("ao", "roughness", "metallic", None),
        "tip": "R = AO, G = Roughness, B = Metallic (glTF, Unreal, Godot)",
    },
    "UNITY_MASK": {
        "name": "Unity HDRP Mask", "suffix": "MaskMap", "alpha": True,
        "channels": ("metallic", "ao", 0.0, "smoothness"),
        "tip": "R = Metallic, G = AO, B = Detail mask (0), A = Smoothness (Unity HDRP)",
    },
    "UNITY_MS": {
        "name": "Unity Metallic-Smoothness", "suffix": "MetallicSmoothness", "alpha": True,
        "channels": ("metallic", "metallic", "metallic", "smoothness"),
        "tip": "R = Metallic, A = Smoothness (Unity URP / Built-in)",
    },
}

EXT = {"PNG": "png", "TIFF": "tif", "OPEN_EXR": "exr"}

# Runtime-only UI state (never saved into the .blend).
_STATE = {"running": False, "progress": 0.0, "text": "", "last_export": []}
_PRESET_LOCK = False

# Tiny time-based cache so redrawing the panel does not re-walk big node trees on every mouse move.
_UI_CACHE = {}


# --------------------------------------------------------------------------- #
#  Small utilities
# --------------------------------------------------------------------------- #

class _Restore:
    """Remember original values of properties so they can be put back afterwards."""

    def __init__(self):
        self._items = []

    def set(self, owner, attr, value):
        if owner is None or not hasattr(owner, attr):
            return
        old = getattr(owner, attr)
        try:
            setattr(owner, attr, value)
        except (TypeError, ValueError, AttributeError) as exc:
            logger.debug("could not set %s: %s", attr, exc)
            return
        self._items.append((owner, attr, old))

    def restore(self):
        for owner, attr, value in reversed(self._items):
            try:
                setattr(owner, attr, value)
            except Exception:  # noqa: BLE001 - best effort restore
                pass
        self._items.clear()


_VALID_ICONS = None


def _icon(name):
    """Return `name` if this Blender version has that icon, else 'NONE' (icons get renamed between releases)."""
    global _VALID_ICONS
    if _VALID_ICONS is None:
        try:
            _VALID_ICONS = frozenset(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())
        except Exception:  # noqa: BLE001 - cannot introspect: trust the names
            _VALID_ICONS = frozenset()
    return name if (not _VALID_ICONS or name in _VALID_ICONS) else "NONE"


def effective_depth(fmt, depth):
    """Clamp the chosen bit depth to something the file format supports."""
    if fmt in {"PNG", "TIFF"}:
        return "16" if depth in {"16", "32"} else "8"
    if fmt == "OPEN_EXR":
        return "32" if depth == "32" else "16"
    return "8"


def set_colorspace(img, name):
    try:
        img.colorspace_settings.name = name
    except (TypeError, ValueError):
        logger.warning("colour space '%s' not available for %s", name, img.name)


def pack_parts(pack):
    """Base map keys a packed texture is built from."""
    return {("roughness" if c == "smoothness" else c) for c in pack["channels"] if isinstance(c, str)}


def describe_pack(pack):
    names = {"ao": "AO", "roughness": "Rough", "metallic": "Metal", "smoothness": "Smooth"}
    out = []
    for letter, c in zip("RGBA", pack["channels"]):
        if c is None:
            continue
        out.append(f"{letter}:{names.get(c, c) if isinstance(c, str) else int(c)}")
    return "  ".join(out)


# --------------------------------------------------------------------------- #
#  Node tree helpers and material analysis
# --------------------------------------------------------------------------- #

def get_output_node(nt):
    node = nt.get_output_node("CYCLES")
    if node is None:
        node = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)
    return node


def find_upstream_node(start_socket, idnames):
    """Breadth-first search upstream of an input socket for a node type."""
    seen = set()
    queue = deque([start_socket])
    while queue:
        sock = queue.popleft()
        for link in sock.links:
            node = link.from_node
            if node.bl_idname in idnames:
                return node
            if node.name in seen:
                continue
            seen.add(node.name)
            queue.extend(i for i in node.inputs if i.is_linked)
    return None


def find_principled(nt, out_node):
    node = find_upstream_node(out_node.inputs["Surface"], {"ShaderNodeBsdfPrincipled"})
    if node is None:
        node = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    return node


def pick_input(node, names):
    for name in names:
        sock = node.inputs.get(name)
        if sock is not None:
            return sock
    return None


def socket_constant(sock):
    """Return the unlinked value of a socket as an RGBA tuple."""
    v = sock.default_value
    if hasattr(v, "__len__"):
        return (float(v[0]), float(v[1]), float(v[2]), 1.0)
    f = float(v)
    return (f, f, f, 1.0)


def find_height_input(out_node, principled):
    """Find a *linked* Height input on a Displacement node (preferred) or a Bump node."""
    disp = out_node.inputs.get("Displacement")
    if disp is not None and disp.is_linked:
        node = find_upstream_node(disp, {"ShaderNodeDisplacement"})
        if node is not None and node.inputs["Height"].is_linked:
            return node.inputs["Height"], node
    if principled is not None:
        nrm = principled.inputs.get("Normal")
        if nrm is not None and nrm.is_linked:
            node = find_upstream_node(nrm, {"ShaderNodeBump"})
            if node is not None and node.inputs["Height"].is_linked:
                return node.inputs["Height"], node
    return None, None


def force_bump_displacement(mat):
    """Make the Cycles normal bake pick up Displacement-node detail as bump."""
    try:
        if hasattr(mat, "displacement_method"):          # Blender 4.2+
            mat.displacement_method = "BUMP"
        elif hasattr(mat, "cycles") and hasattr(mat.cycles, "displacement_method"):
            mat.cycles.displacement_method = "BUMP"
    except (TypeError, ValueError):
        pass


class Analysis:
    """What can be extracted from a material (used by the UI preview and by the exporter)."""

    def __init__(self):
        self.problem = ""          # fatal problem (nothing can be exported)
        self.warning = ""          # non-fatal remark
        self.out_node = None
        self.principled = None
        self.sources = {}          # key -> ("link", socket) | ("const", rgba)
        self.reasons = {}          # key -> why it is unavailable
        self.height_node = None
        self.has_normal = False
        self.available = set()


def analyze_material(mat, ao_method="HEIGHT"):
    a = Analysis()
    nt = mat.node_tree
    if nt is None or getattr(mat, "is_grease_pencil", False):
        a.problem = "Material has no shader nodes"
        return a
    out = get_output_node(nt)
    if out is None:
        a.problem = "No Material Output node"
        return a
    a.out_node = out
    pr = find_principled(nt, out)
    a.principled = pr
    if pr is None:
        a.warning = "No top-level Principled BSDF (not inside a group). Only Normal/Height possible."

    for key in ("basecolor", "roughness", "metallic", "emission", "opacity"):
        if pr is None:
            a.reasons[key] = "no Principled BSDF"
            continue
        sock = pick_input(pr, MAP_INFO[key]["sockets"])
        if sock is None:
            a.reasons[key] = "input not found on the Principled BSDF"
        elif sock.is_linked:
            a.sources[key] = ("link", sock.links[0].from_socket)
        else:
            const = socket_constant(sock)
            strength = pr.inputs.get("Emission Strength")
            strength_off = strength is not None and not strength.is_linked and strength.default_value == 0.0
            if key == "emission" and (max(const[:3]) <= 1e-6 or strength_off):
                a.reasons[key] = "material does not emit light"
            elif key == "opacity" and const[0] >= 0.999:
                a.reasons[key] = "material is fully opaque"
            else:
                a.sources[key] = ("const", const)

    height_in, a.height_node = find_height_input(out, pr)
    if height_in is not None:
        a.sources["height"] = ("link", height_in.links[0].from_socket)
    else:
        a.reasons["height"] = "no Displacement/Bump node with a connected Height input"

    a.has_normal = out.inputs["Displacement"].is_linked or (pr is not None and pr.inputs["Normal"].is_linked)
    if not a.has_normal:
        a.reasons["normal"] = "no Normal, Bump or Displacement connected (it would be flat)"

    a.available = set(a.sources)
    if a.has_normal:
        a.available.add("normal")
    if ao_method == "CYCLES" or "height" in a.sources:
        a.available.add("ao")
    else:
        a.reasons["ao"] = "AO from height needs a Displacement/Bump height source"
    if "height" in a.sources:
        a.available.add("curvature")
    else:
        a.reasons["curvature"] = "curvature is computed from a height source (Displacement/Bump)"
    return a


class AnalysisSummary:
    """Plain-data copy of an Analysis. Holds no Blender pointers, so it is safe to cache."""

    def __init__(self, a):
        self.problem = a.problem
        self.warning = a.warning
        self.available = frozenset(a.available)
        self.const_keys = frozenset(k for k, v in a.sources.items() if v[0] == "const")


def _cached(key, compute, ttl=0.5):
    now = time.monotonic()
    hit = _UI_CACHE.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    value = compute()
    if len(_UI_CACHE) > 16:
        _UI_CACHE.clear()
    _UI_CACHE[key] = (now, value)
    return value


def plan_export(a, s):
    """
    Decide what to bake. Returns (wanted, need, can_pack):
      wanted   maps the user switched on
      need     maps that must exist in memory (wanted + pack parts + height for derived maps),
               limited to what is available
      can_pack whether the chosen packed texture can be built
    """
    wanted = {k for k in MAP_ORDER if getattr(s, "map_" + k)}
    pack = PACKS.get(s.pack_type)
    parts = pack_parts(pack) if pack else set()
    need = (wanted | parts) & a.available
    can_pack = bool(pack) and {"roughness", "metallic"} <= need
    if pack and not can_pack:
        need = wanted & a.available
    if ("ao" in need and s.ao_method == "HEIGHT") or "curvature" in need:
        need.add("height")
    return wanted, need, can_pack


def is_derived(key, s):
    """Maps computed from the height map instead of being baked."""
    return key == "curvature" or (key == "ao" and s.ao_method == "HEIGHT")


# --------------------------------------------------------------------------- #
#  Baking primitives
# --------------------------------------------------------------------------- #

def bake_native(nt, img, bake_type):
    """Bake a Cycles pass (NORMAL / AO) straight into `img`."""
    node = nt.nodes.new("ShaderNodeTexImage")
    node.image = img
    node.select = True
    nt.nodes.active = node
    try:
        bpy.ops.object.bake(type=bake_type)
    finally:
        nt.nodes.remove(node)


def bake_emission(nt, out_node, img, source_socket=None, constant=None):
    """
    Bake one channel by feeding it through an Emission shader.

    source_socket: output socket whose value should be baked, or
    constant:      an RGBA tuple to bake when nothing is connected.
    """
    surf = out_node.inputs["Surface"]
    orig_from = surf.links[0].from_socket if surf.is_linked else None

    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 1.0
    img_node = nt.nodes.new("ShaderNodeTexImage")
    img_node.image = img
    try:
        if source_socket is not None:
            nt.links.new(source_socket, emit.inputs["Color"])
        else:
            emit.inputs["Color"].default_value = constant
        nt.links.new(emit.outputs["Emission"], surf)
        img_node.select = True
        nt.nodes.active = img_node
        bpy.ops.object.bake(type="EMIT")
    finally:
        nt.nodes.remove(emit)
        nt.nodes.remove(img_node)
        if orig_from is not None:
            nt.links.new(orig_from, surf)


# --------------------------------------------------------------------------- #
#  Image I/O and pixel maths
# --------------------------------------------------------------------------- #

def _pick_view_transform(scene, want_raw):
    """
    Name of the view transform that leaves pixel values alone ('Raw') or applies the plain
    sRGB encoding ('Standard'). Returns None when this Blender's colour config lacks it.
    """
    wanted = "Raw" if want_raw else "Standard"
    try:
        ids = {i.identifier for i in scene.view_settings.bl_rna.properties["view_transform"].enum_items}
    except Exception:  # noqa: BLE001 - cannot introspect, just try the name
        return wanted
    return wanted if wanted in ids else None


def save_image(img, filepath, fmt, depth, mode, is_data):
    """
    Save through a throw-away scene so we fully control colour management:
    data maps and EXR are written untouched ('Raw'), colour maps get the
    standard sRGB encoding ('Standard') instead of the user's Filmic/AgX look.
    mode: 'BW', 'RGB' or 'RGBA'.

    If the needed view transform does not exist in this Blender's colour config, the image is
    written with Image.save() instead (correct colour space, but no bit-depth control).
    """
    want_raw = is_data or fmt == "OPEN_EXR"
    scene = bpy.data.scenes.new(TMP + "save")
    try:
        view = _pick_view_transform(scene, want_raw)
        if view is None:
            logger.warning("view transform for %s not available; falling back to Image.save()", img.name)
            img.filepath_raw = filepath
            img.file_format = fmt
            img.save()
            return
        ims = scene.render.image_settings
        ims.file_format = fmt
        ims.color_mode = mode
        ims.color_depth = depth
        if fmt == "PNG":
            ims.compression = 15
        try:
            scene.render.dither_intensity = 0.0   # no noise when going float -> 8 bit
        except (AttributeError, TypeError):
            pass
        scene.view_settings.view_transform = view
        img.save_render(filepath, scene=scene)
    finally:
        bpy.data.scenes.remove(scene)


def read_channel(img, channel=0):
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf.reshape(h, w, 4)[:, :, channel].copy()


def gray_to_rgba_flat(arr):
    h, w = arr.shape
    out = np.ones((h, w, 4), dtype=np.float32)
    out[:, :, 0] = arr
    out[:, :, 1] = arr
    out[:, :, 2] = arr
    return out.reshape(-1)


def write_gray(img, arr):
    img.pixels.foreach_set(gray_to_rgba_flat(arr))
    img.update()


def pack_channels(channels, arrays, shape):
    """
    Build a flat RGBA array from a channel spec.
    channels: 4 entries of map key / 'smoothness' / constant / None
    arrays:   {map key: 2D array}
    Missing AO becomes 1.0, any other missing source 0.0.
    """
    h, w = shape
    out = np.zeros((h, w, 4), dtype=np.float32)
    out[:, :, 3] = 1.0
    for i, src in enumerate(channels):
        if src is None:
            continue
        if isinstance(src, (int, float)):
            out[:, :, i] = float(src)
        elif src == "smoothness":
            out[:, :, i] = 1.0 - arrays["roughness"]
        else:
            arr = arrays.get(src)
            out[:, :, i] = arr if arr is not None else (1.0 if src == "ao" else 0.0)
    return out.reshape(-1)


def normalize01(arr):
    lo, hi = float(arr.min()), float(arr.max())
    if hi - lo < 1e-8:
        return arr
    return ((arr - lo) / (hi - lo)).astype(np.float32)


def gaussian_blur_wrap(arr, sigma):
    """Gaussian blur with wrap-around edges (keeps tileable textures tileable)."""
    h, w = arr.shape
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    kernel = np.exp(-2.0 * (math.pi * sigma) ** 2 * (fx * fx + fy * fy))
    return np.fft.irfft2(np.fft.rfft2(arr) * kernel, s=arr.shape).astype(np.float32)


def curvature_from_height(height, radius=0.005, strength=1.0):
    """
    Curvature map from a height field: 0.5 = flat, brighter = convex (edges), darker = concave (cavities).
    radius is the feature size as a fraction of the tile width.
    """
    n = max(height.shape)
    sigma = max(0.8, radius * n)
    h32 = height.astype(np.float32)
    diff = h32 - gaussian_blur_wrap(h32, sigma)
    std = float(diff.std())
    if std < 1e-9:
        return np.full(height.shape, 0.5, dtype=np.float32)
    return np.clip(0.5 + 0.25 * strength * diff / std, 0.0, 1.0).astype(np.float32)


def height_to_ao(height, relief=0.05, radius=0.05, strength=1.0, directions=8, samples=10):
    """
    Approximate ambient occlusion from a height map (horizon-based, like HBAO on a heightfield).

    height   2D float array, nominally 0..1, treated as TILEABLE (edges wrap).
    relief   height of the relief as a fraction of the tile width (1.0 = as tall as the tile is wide).
    radius   search radius as a fraction of the tile width.
    strength AO multiplier.
    Returns a 2D float array in 0..1 (1 = unoccluded).
    """
    n = max(height.shape)
    hp = height.astype(np.float32) * np.float32(relief * n)  # relief in pixel units
    max_r = max(2.0, radius * n)
    dists = np.unique(np.round(np.geomspace(1.0, max_r, samples)).astype(int))

    occ = np.zeros_like(hp)
    for i in range(directions):
        angle = 2.0 * math.pi * i / directions
        best = np.zeros_like(hp)  # steepest upward slope seen along this direction
        for d in dists:
            dx = int(round(d * math.cos(angle)))
            dy = int(round(d * math.sin(angle)))
            if dx == 0 and dy == 0:
                continue
            dist = math.hypot(dx, dy)
            sample = np.roll(hp, shift=(-dy, -dx), axis=(0, 1))
            np.maximum(best, (sample - hp) / dist, out=best)
        occ += best / np.sqrt(1.0 + best * best)  # sin(atan(slope))
    ao = 1.0 - strength * occ / directions
    return np.clip(ao, 0.0, 1.0)


# --------------------------------------------------------------------------- #
#  Temporary bake target
# --------------------------------------------------------------------------- #

def make_plane(scene, size, mat):
    half = size / 2.0
    me = bpy.data.meshes.new(TMP + "plane")
    me.from_pydata([(-half, -half, 0), (half, -half, 0), (half, half, 0), (-half, half, 0)], [], [(0, 1, 2, 3)])
    me.update()
    uv = me.uv_layers.new(name="UVMap")
    for i, co in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
        uv.data[i].uv = co
    me.materials.append(mat)
    obj = bpy.data.objects.new(TMP + "plane", me)
    scene.collection.objects.link(obj)
    return obj


def remove_plane(obj):
    me = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(me)


# --------------------------------------------------------------------------- #
#  Presets
# --------------------------------------------------------------------------- #

def _maps(**kw):
    base = {k: False for k in MAP_ORDER}
    base.update(kw)
    return {"map_" + k: v for k, v in base.items()}


PRESETS = {
    "GENERIC": dict(normal_convention="OPENGL", naming="GENERIC", pack_type="NONE",
                    **_maps(basecolor=True, normal=True, roughness=True, metallic=True,
                            height=True, ao=True, emission=True, opacity=True)),
    "UNREAL": dict(normal_convention="DIRECTX", naming="UNREAL", pack_type="ORM",
                   **_maps(basecolor=True, normal=True, height=True, emission=True, opacity=True)),
    "UNITY_URP": dict(normal_convention="OPENGL", naming="ALBEDO", pack_type="UNITY_MS",
                      **_maps(basecolor=True, normal=True, height=True, ao=True, emission=True, opacity=True)),
    "UNITY_HDRP": dict(normal_convention="OPENGL", naming="ALBEDO", pack_type="UNITY_MASK",
                       **_maps(basecolor=True, normal=True, height=True, emission=True, opacity=True)),
    "GLTF": dict(normal_convention="OPENGL", naming="GENERIC", pack_type="ORM",
                 **_maps(basecolor=True, normal=True, emission=True, opacity=True)),
    "FULL": dict(normal_convention="OPENGL", naming="GENERIC", pack_type="ORM",
                 **_maps(basecolor=True, normal=True, roughness=True, metallic=True, height=True,
                         ao=True, emission=True, opacity=True, curvature=True)),
}


def _on_preset(self, context):
    global _PRESET_LOCK
    values = PRESETS.get(self.preset)
    if values is None:      # "CUSTOM"
        return
    _PRESET_LOCK = True
    try:
        for key, value in values.items():
            setattr(self, key, value)
    finally:
        _PRESET_LOCK = False


def _mark_custom(self, context):
    if _PRESET_LOCK:
        return
    if self.preset != "CUSTOM":
        self.preset = "CUSTOM"


def _map_prop(label, default):
    return BoolProperty(name=label, default=default, update=_mark_custom)


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #

class MATBAKER_Settings(PropertyGroup):
    ui_mode: EnumProperty(
        name="Mode",
        items=[
            ("SIMPLE", "Simple", "Preset + resolution + one button"),
            ("ADVANCED", "Advanced", "Show every setting"),
        ],
        default="SIMPLE",
    )
    preset: EnumProperty(
        name="Preset",
        items=[
            ("GENERIC", "Generic / Blender", "Separate maps, OpenGL normals"),
            ("UNREAL", "Unreal Engine", "BaseColor + Normal (DirectX) + ORM packed"),
            ("UNITY_URP", "Unity URP / Built-in", "Albedo + Normal + AO + Metallic-Smoothness packed"),
            ("UNITY_HDRP", "Unity HDRP", "Albedo + Normal + Mask map packed"),
            ("GLTF", "glTF / Godot / Three.js", "BaseColor + Normal + ORM packed"),
            ("FULL", "Everything", "All maps, including curvature, plus ORM"),
            ("CUSTOM", "Custom", "You changed the maps/packing/normal settings"),
        ],
        default="GENERIC",
        update=_on_preset,
    )

    output_dir: StringProperty(
        name="Folder", subtype="DIR_PATH", default="//pbr_export/",
        description="Where the texture pack is written ('//' is relative to the .blend file)",
    )
    scope: EnumProperty(
        name="Materials",
        items=[
            ("ACTIVE", "Active Material", "Export the active material of the active object"),
            ("SELECTED", "Selected Objects", "Export every material used by the selected objects"),
            ("ALL", "All Materials in File", "Export every node-based material in this .blend file"),
        ],
        default="ACTIVE",
    )
    bake_mode: EnumProperty(
        name="Bake On",
        items=[
            ("PLANE", "Flat Plane", "Bake onto a temporary plane with a 0-1 UV map (tileable texture packs)"),
            ("OBJECT", "Object's UVs", "Bake onto the owning object using its own UV map"),
        ],
        default="PLANE",
    )
    plane_size: FloatProperty(
        name="Plane Size", default=2.0, min=0.001, max=1000.0, subtype="DISTANCE",
        description="Size of the temporary plane. 2 m matches Blender's default plane, so "
                    "materials that use Object coordinates look the same as in your viewport",
    )

    resolution: EnumProperty(
        name="Resolution",
        items=[
            ("256", "256", "256 x 256 px"),
            ("512", "512", "512 x 512 px"),
            ("1024", "1K", "1024 x 1024 px"),
            ("2048", "2K", "2048 x 2048 px"),
            ("4096", "4K", "4096 x 4096 px"),
            ("8192", "8K", "8192 x 8192 px (needs a lot of RAM)"),
        ],
        default="2048",
    )
    file_format: EnumProperty(
        name="Format",
        items=[
            ("PNG", "PNG", ""),
            ("TIFF", "TIFF", ""),
            ("OPEN_EXR", "OpenEXR", "Float, linear, untouched data"),
        ],
        default="PNG",
    )
    color_depth: EnumProperty(
        name="Bit Depth",
        items=[
            ("8", "8-bit", ""),
            ("16", "16-bit", "PNG/TIFF: 16-bit integer. EXR: half float"),
            ("32", "32-bit", "EXR only (PNG/TIFF fall back to 16-bit)"),
        ],
        default="8",
    )
    normal_convention: EnumProperty(
        name="Normal Y",
        items=[
            ("OPENGL", "OpenGL (+Y)", "Blender, Unity, glTF, Godot"),
            ("DIRECTX", "DirectX (-Y)", "Unreal Engine, 3ds Max"),
        ],
        default="OPENGL",
        update=_mark_custom,
    )
    naming: EnumProperty(
        name="Naming",
        items=[
            ("GENERIC", "Name_BaseColor", "BaseColor, Normal, Roughness, Metallic, Height, AO, ..."),
            ("ALBEDO", "Name_Albedo", "Albedo instead of BaseColor"),
            ("UNREAL", "Name_BC / _N / _R", "Short Unreal-style suffixes"),
        ],
        default="GENERIC",
        update=_mark_custom,
    )

    map_basecolor: _map_prop("Base Color", True)
    map_normal: _map_prop("Normal", True)
    map_roughness: _map_prop("Roughness", True)
    map_metallic: _map_prop("Metallic", True)
    map_height: _map_prop("Height", True)
    map_ao: _map_prop("Ambient Occlusion", True)
    map_emission: _map_prop("Emission", True)
    map_opacity: _map_prop("Opacity", True)
    map_curvature: _map_prop("Curvature", False)

    pack_type: EnumProperty(
        name="Packed Texture",
        items=[
            ("NONE", "None", "Do not write a packed texture"),
            ("ORM", "ORM", PACKS["ORM"]["tip"]),
            ("UNITY_MASK", "Unity HDRP Mask", PACKS["UNITY_MASK"]["tip"]),
            ("UNITY_MS", "Unity Metallic-Smoothness", PACKS["UNITY_MS"]["tip"]),
        ],
        default="NONE",
        update=_mark_custom,
    )

    ao_method: EnumProperty(
        name="AO Method",
        items=[
            ("HEIGHT", "From Height", "Approximate AO from the height map. Best for flat tileable textures; needs a Displacement or Bump node"),
            ("CYCLES", "Cycles Bake", "Real ray-traced AO using the World's Ambient Occlusion settings. Needs real geometry (use 'Object's UVs')"),
        ],
        default="HEIGHT",
    )
    ao_relief: FloatProperty(
        name="AO Relief", default=0.05, min=0.0, max=1.0,
        description="How tall the height map is, as a fraction of the tile width (bigger = deeper AO)",
    )
    ao_radius: FloatProperty(
        name="AO Radius", default=0.05, min=0.002, max=0.5,
        description="How far occluders are searched for, as a fraction of the tile width",
    )
    ao_strength: FloatProperty(name="AO Strength", default=1.0, min=0.0, max=4.0)
    ao_samples: IntProperty(name="Cycles AO Samples", default=64, min=1, max=4096)

    curv_radius: FloatProperty(
        name="Curvature Radius", default=0.005, min=0.0005, max=0.1,
        description="Feature size for curvature, as a fraction of the tile width (small = fine edges)",
    )
    curv_strength: FloatProperty(name="Curvature Strength", default=1.0, min=0.0, max=4.0)
    normalize_height: BoolProperty(
        name="Normalize Height", default=False,
        description="Stretch the height map to use the full 0-1 range (the original range is stored in the JSON manifest)",
    )

    samples: IntProperty(
        name="Samples", default=16, min=1, max=4096,
        description="Cycles samples per pixel (anti-aliasing of the baked maps)",
    )
    margin: IntProperty(name="Margin", default=16, min=0, max=64, subtype="PIXEL")
    use_subfolder: BoolProperty(name="Subfolder per Material", default=True)
    write_manifest: BoolProperty(
        name="Write JSON Manifest", default=True,
        description="Write <material>_pbr.json with resolution, normal convention, file names and height info",
    )
    keep_images: BoolProperty(
        name="Keep Images in Blender", default=False,
        description="Keep the baked images in this .blend instead of deleting them after saving",
    )


def needs_numpy(s):
    return (s.pack_type != "NONE" or s.normalize_height or s.map_curvature
            or (s.map_ao and s.ao_method == "HEIGHT"))


# --------------------------------------------------------------------------- #
#  Export of a single material (generator: yields progress between steps)
# --------------------------------------------------------------------------- #

def export_material_steps(context, src_mat, owner, s, out_root, rs, log):
    """
    Bake and save one material.

    Yields (fraction 0..1, text) before each slow step so the UI can update.
    Returns an info dict (or None if nothing was exported). Cleanup always runs,
    even when the generator is closed early (Esc).
    """
    scene = context.scene
    view_layer = context.view_layer

    if src_mat.node_tree is None or getattr(src_mat, "is_grease_pencil", False):
        log.append(("WARNING", f"{src_mat.name}: no shader node tree, skipped"))
        return None
    if s.bake_mode == "OBJECT":
        if owner is None:
            log.append(("WARNING", f"{src_mat.name}: not used by any object, skipped (use 'Flat Plane' instead)"))
            return None
        if owner.type != "MESH":
            log.append(("WARNING", f"{src_mat.name}: '{owner.name}' is not a mesh, skipped"))
            return None
        if len(owner.data.uv_layers) == 0:
            log.append(("WARNING", f"{src_mat.name}: '{owner.name}' has no UV map. Unwrap it or use 'Flat Plane'."))
            return None
        if not owner.visible_get():
            log.append(("WARNING", f"{src_mat.name}: '{owner.name}' is hidden, skipped"))
            return None

    res = int(s.resolution)
    fmt = s.file_format
    depth = effective_depth(fmt, s.color_depth)
    use_float = depth != "8"
    ext = EXT[fmt]
    suffix = SUFFIXES[s.naming]
    mat_name = bpy.path.clean_name(src_mat.name)
    folder = os.path.join(out_root, mat_name) if s.use_subfolder else out_root
    os.makedirs(folder, exist_ok=True)

    mat = src_mat.copy()
    mat.name = TMP + mat_name
    nt = mat.node_tree
    baked = {}
    slot_backup = None
    plane = None
    written = {}

    try:
        a = analyze_material(mat, s.ao_method)
        if a.problem:
            log.append(("WARNING", f"{src_mat.name}: {a.problem}, skipped"))
            return None
        if a.warning:
            log.append(("WARNING", f"{src_mat.name}: {a.warning}"))
        out_node, pr = a.out_node, a.principled
        force_bump_displacement(mat)

        wanted, need, can_pack = plan_export(a, s)
        pack = PACKS.get(s.pack_type)
        if pack and not can_pack:
            log.append(("WARNING", f"{src_mat.name}: {pack['name']} texture skipped (needs Roughness and Metallic)"))
        for key in MAP_ORDER:
            if key in wanted and key not in need:
                log.append(("INFO", f"{src_mat.name}: {MAP_INFO[key]['label']} skipped ({a.reasons.get(key, 'nothing to bake')})"))
        if not need:
            log.append(("WARNING", f"{src_mat.name}: nothing to export"))
            return None

        # ---- set up the bake target --------------------------------------------------
        if s.bake_mode == "OBJECT":
            target = owner
            slot_backup = [(sl, sl.material) for sl in owner.material_slots]
            for sl in owner.material_slots:
                sl.material = mat
        else:
            plane = make_plane(scene, s.plane_size, mat)
            target = plane
        view_layer.update()
        for o in view_layer.objects:
            try:
                o.select_set(False)
            except RuntimeError:
                pass
        target.select_set(True)
        view_layer.objects.active = target

        def new_image(key, is_data, alpha=False, force_float=False):
            img = bpy.data.images.new(f"{TMP}{mat_name}_{key}", res, res, alpha=alpha,
                                      float_buffer=(use_float or force_float))
            set_colorspace(img, "Non-Color" if is_data else "sRGB")
            if alpha:
                try:
                    img.alpha_mode = "CHANNEL_PACKED"   # keep packed channels untouched on save
                except (TypeError, ValueError):
                    pass
            return img

        # ---- bake --------------------------------------------------------------------
        order = [k for k in ("normal", "ao", "basecolor", "roughness", "metallic", "height", "emission", "opacity")
                 if k in need and not is_derived(k, s)]
        total_steps = len(order) + 1
        cycles = getattr(scene, "cycles", None)

        for idx, key in enumerate(order):
            label = MAP_INFO[key]["label"]
            yield idx / total_steps, f"baking {label}"
            logger.info("%s: baking %s", src_mat.name, label)
            img = new_image(key, MAP_INFO[key]["data"], force_float=(key == "height" and s.normalize_height))
            baked[key] = img
            if key == "normal":
                rs.set(cycles, "samples", s.samples)
                bake_native(nt, img, "NORMAL")
            elif key == "ao":
                rs.set(cycles, "samples", s.ao_samples)
                bake_native(nt, img, "AO")
            else:
                kind, src = a.sources[key]
                rs.set(cycles, "samples", s.samples if kind == "link" else 1)
                if kind == "link":
                    bake_emission(nt, out_node, img, source_socket=src)
                else:
                    bake_emission(nt, out_node, img, constant=src)

        # ---- derived maps ------------------------------------------------------------
        yield len(order) / total_steps, "processing and saving"
        height_range = None
        height_arr = None
        derived = [k for k in need if is_derived(k, s)]
        if "height" in baked and (s.normalize_height or derived):
            height_arr = read_channel(baked["height"])
            if s.normalize_height:
                height_range = (float(height_arr.min()), float(height_arr.max()))
                height_arr = normalize01(height_arr)
                write_gray(baked["height"], height_arr)
        if "ao" in derived:
            logger.info("%s: computing AO from height", src_mat.name)
            img = new_image("ao", True)
            write_gray(img, height_to_ao(height_arr, s.ao_relief, s.ao_radius, s.ao_strength))
            baked["ao"] = img
        if "curvature" in derived:
            logger.info("%s: computing curvature from height", src_mat.name)
            img = new_image("curvature", True)
            write_gray(img, curvature_from_height(height_arr, s.curv_radius, s.curv_strength))
            baked["curvature"] = img

        # ---- save --------------------------------------------------------------------
        for key in MAP_ORDER:
            if key in wanted and key in baked:
                path = os.path.join(folder, f"{mat_name}_{suffix[key]}.{ext}")
                mode = "BW" if MAP_INFO[key]["bw"] else "RGB"
                save_image(baked[key], path, fmt, depth, mode, MAP_INFO[key]["data"])
                written[key] = os.path.basename(path)

        pack_file = None
        if can_pack:
            arrays = {k: read_channel(baked[k]) for k in pack_parts(pack) if k in baked}
            shape = arrays["roughness"].shape
            img = new_image("pack", True, alpha=pack["alpha"])
            img.pixels.foreach_set(pack_channels(pack["channels"], arrays, shape))
            img.update()
            baked["pack"] = img
            pack_file = f"{mat_name}_{pack['suffix']}.{ext}"
            save_image(img, os.path.join(folder, pack_file), fmt, depth, "RGBA" if pack["alpha"] else "RGB", True)

        # ---- manifest ----------------------------------------------------------------
        if s.write_manifest and (written or pack_file):
            manifest = {
                "material": src_mat.name,
                "generator": f"Blender {bpy.app.version_string} / Mat-Baker",
                "resolution": res,
                "format": fmt,
                "bit_depth": int(depth),
                "normal_convention": "DirectX" if s.normal_convention == "DIRECTX" else "OpenGL",
                "maps": written,
            }
            if pack_file:
                manifest["packed"] = {"type": pack["name"], "file": pack_file, "channels": describe_pack(pack)}
            height_info = {}
            if a.height_node is not None and a.height_node.bl_idname == "ShaderNodeDisplacement":
                height_info["midlevel"] = float(a.height_node.inputs["Midlevel"].default_value)
                height_info["scale"] = float(a.height_node.inputs["Scale"].default_value)
            if height_range is not None:
                height_info["normalized"] = True
                height_info["original_range"] = list(height_range)
            if height_info:
                manifest["height"] = height_info
            if pr is not None:
                st = pr.inputs.get("Emission Strength")
                if "emission" in written and st is not None and not st.is_linked:
                    manifest["emission_strength"] = float(st.default_value)
                io = pr.inputs.get("IOR")
                if io is not None and not io.is_linked:
                    manifest["ior"] = float(io.default_value)
            with open(os.path.join(folder, f"{mat_name}_pbr.json"), "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2)

        count = len(written) + (1 if pack_file else 0)
        log.append(("INFO", f"{src_mat.name}: wrote {count} file(s) to {folder}"))
        return {
            "material": src_mat.name,
            "folder": folder,
            "maps": written,
            "pack": s.pack_type if pack_file else "NONE",
            "pack_file": pack_file,
            "normal": s.normal_convention,
            "count": count,
        }

    finally:
        if slot_backup is not None:
            for sl, original in slot_backup:
                try:
                    sl.material = original
                except Exception:  # noqa: BLE001
                    pass
        if plane is not None:
            remove_plane(plane)
        bpy.data.materials.remove(mat)
        for key, img in baked.items():
            if s.keep_images and key in written:
                img.name = os.path.splitext(written[key])[0]
                img.use_fake_user = True
            else:
                bpy.data.images.remove(img)


def run_export(context, s, jobs, out_root, rs, log, results):
    """Generator over all materials. Yields (overall fraction, status text)."""
    n = len(jobs)
    for i, (mat, owner) in enumerate(jobs):
        gen = export_material_steps(context, mat, owner, s, out_root, rs, log)
        info = None
        try:
            while True:
                try:
                    frac, text = next(gen)
                except StopIteration as stop:
                    info = stop.value
                    break
                yield (i + frac) / n, f"[{i + 1}/{n}] {mat.name}: {text}"
        except Exception as exc:  # noqa: BLE001 - keep going with the next material
            logger.exception("Mat-Baker error")
            log.append(("ERROR", f"{mat.name}: {exc}"))
        finally:
            gen.close()
        if info:
            results.append(info)
    yield 1.0, "done"


# --------------------------------------------------------------------------- #
#  Preview material built from an export
# --------------------------------------------------------------------------- #

def build_preview_material(entry):
    """Create (or refresh) a Principled material that uses the exported files."""
    name = f"{bpy.path.clean_name(entry['material'])}_PBR"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except Exception:  # noqa: BLE001 - deprecated in newest versions
        pass
    mat["mat_baker_preview"] = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (1000, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (700, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    folder, files = entry["folder"], entry["maps"]
    state = {"y": 600}

    def node(idname, x, y):
        n = nt.nodes.new(idname)
        n.location = (x, y)
        return n

    def tex(filename, is_data, packed=False):
        path = os.path.join(folder, filename)
        img = bpy.data.images.load(path, check_existing=True)
        try:
            img.reload()
        except RuntimeError:
            pass
        if is_data:
            set_colorspace(img, "Non-Color")
        elif not filename.lower().endswith(".exr"):
            set_colorspace(img, "sRGB")
        if packed:
            try:
                img.alpha_mode = "CHANNEL_PACKED"
            except (TypeError, ValueError):
                pass
        n = node("ShaderNodeTexImage", -200, state["y"])
        n.image = img
        state["y"] -= 320
        return n

    if "basecolor" in files:
        nt.links.new(tex(files["basecolor"], False).outputs["Color"], bsdf.inputs["Base Color"])
    if "roughness" in files:
        nt.links.new(tex(files["roughness"], True).outputs["Color"], bsdf.inputs["Roughness"])
    if "metallic" in files:
        nt.links.new(tex(files["metallic"], True).outputs["Color"], bsdf.inputs["Metallic"])
    if "emission" in files:
        sock = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
        nt.links.new(tex(files["emission"], False).outputs["Color"], sock)
        if bsdf.inputs.get("Emission Strength") is not None:
            bsdf.inputs["Emission Strength"].default_value = 1.0
    if "opacity" in files:
        nt.links.new(tex(files["opacity"], True).outputs["Color"], bsdf.inputs["Alpha"])
        for attr, value in (("blend_method", "HASHED"), ("surface_render_method", "DITHERED")):
            if hasattr(mat, attr):
                try:
                    setattr(mat, attr, value)
                except (TypeError, ValueError):
                    pass

    if "normal" in files:
        t = tex(files["normal"], True)
        color = t.outputs["Color"]
        if entry.get("normal") == "DIRECTX":     # flip green so Blender (OpenGL) reads it correctly
            sep = node("ShaderNodeSeparateColor", 100, t.location[1])
            inv = node("ShaderNodeMath", 300, t.location[1] - 120)
            inv.operation = "SUBTRACT"
            inv.inputs[0].default_value = 1.0
            comb = node("ShaderNodeCombineColor", 450, t.location[1])
            nt.links.new(color, sep.inputs["Color"])
            nt.links.new(sep.outputs["Red"], comb.inputs["Red"])
            nt.links.new(sep.outputs["Green"], inv.inputs[1])
            nt.links.new(inv.outputs["Value"], comb.inputs["Green"])
            nt.links.new(sep.outputs["Blue"], comb.inputs["Blue"])
            color = comb.outputs["Color"]
        nmap = node("ShaderNodeNormalMap", 450, t.location[1] - 250)
        nt.links.new(color, nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])

    if "height" in files:
        t = tex(files["height"], True)
        disp = node("ShaderNodeDisplacement", 700, -450)
        disp.inputs["Midlevel"].default_value = 0.5
        disp.inputs["Scale"].default_value = 0.05
        nt.links.new(t.outputs["Color"], disp.inputs["Height"])
        nt.links.new(disp.outputs["Displacement"], out.inputs["Displacement"])

    # Roughness / metallic that only exist inside a packed texture
    pack = PACKS.get(entry.get("pack", "NONE"))
    if pack and entry.get("pack_file"):
        t = tex(entry["pack_file"], True, packed=True)
        sep = node("ShaderNodeSeparateColor", 100, t.location[1])
        nt.links.new(t.outputs["Color"], sep.inputs["Color"])
        done = set()
        for i, src in enumerate(pack["channels"]):
            if not isinstance(src, str) or src in done or src == "ao":
                continue
            target = "Metallic" if src == "metallic" else "Roughness"
            if target.lower() in files:      # a separate map already drives it
                continue
            socket = sep.outputs[("Red", "Green", "Blue")[i]] if i < 3 else t.outputs["Alpha"]
            if src == "smoothness":
                inv = node("ShaderNodeMath", 300, t.location[1] - 150)
                inv.operation = "SUBTRACT"
                inv.inputs[0].default_value = 1.0
                nt.links.new(socket, inv.inputs[1])
                socket = inv.outputs["Value"]
            nt.links.new(socket, bsdf.inputs[target])
            done.add(src)
    return mat


# --------------------------------------------------------------------------- #
#  Operators
# --------------------------------------------------------------------------- #

def collect_jobs(context, scope):
    """Return [(material, owner_object_or_None)]."""
    jobs, seen = [], set()
    if scope == "ACTIVE":
        obj = context.active_object
        mat = obj.active_material if obj else None
        if mat is not None:
            jobs.append((mat, obj))
    elif scope == "SELECTED":
        for obj in context.selected_objects:
            for slot in obj.material_slots:
                mat = slot.material
                if mat is not None and mat.name not in seen:
                    seen.add(mat.name)
                    jobs.append((mat, obj))
    else:  # ALL
        owners = {}
        for obj in context.scene.objects:
            for slot in obj.material_slots:
                m = slot.material
                if m is not None and m.name not in owners:
                    owners[m.name] = obj
        for mat in bpy.data.materials:
            if mat.name.startswith(TMP) or mat.get("mat_baker_preview"):
                continue
            if mat.node_tree is None or mat.users == 0 or getattr(mat, "is_grease_pencil", False):
                continue
            jobs.append((mat, owners.get(mat.name)))
    return jobs


def _redraw_all(context):
    try:
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


class MATBAKER_OT_export(Operator):
    bl_idname = "mat_baker.export_pack"
    bl_label = "Export PBR Texture Pack"
    bl_description = "Bake the procedural material(s) into a PBR texture pack on disk (Esc cancels)"
    bl_options = {"REGISTER"}

    _timer = None
    _gen = None

    @classmethod
    def poll(cls, context):
        return (not _STATE["running"]) and (context.active_object is not None or bool(context.selected_objects))

    # -- setup / teardown ----------------------------------------------------------
    def _begin(self, context):
        """Prepare everything. Returns an error string, or None when ready."""
        scene = context.scene
        s = scene.mat_baker

        if s.output_dir.startswith("//") and not bpy.data.filepath:
            return "Save the .blend file first, or choose an absolute output folder."
        out_root = bpy.path.abspath(s.output_dir)
        try:
            os.makedirs(out_root, exist_ok=True)
        except OSError as exc:
            return f"Cannot create output folder: {exc}"
        if needs_numpy(s) and np is None:
            return "NumPy is required for packing / AO / curvature / normalizing but is not available."
        if not hasattr(scene, "cycles"):
            return "The Cycles add-on is disabled. Enable it in Preferences > Add-ons (baking needs it)."
        jobs = collect_jobs(context, s.scope)
        if not jobs:
            return "No node-based material found for the chosen scope."

        obj = context.active_object
        if obj is not None and obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        self._log = []
        self._results = []
        self._out_root = out_root
        self._view_layer = context.view_layer
        self._prev_active = self._view_layer.objects.active
        self._prev_selected = [o for o in self._view_layer.objects if o.select_get()]

        rs = _Restore()
        self._rs = rs
        rs.set(scene.render, "engine", "CYCLES")
        if scene.render.engine != "CYCLES":
            rs.restore()
            return "Could not switch the render engine to Cycles."
        bake = scene.render.bake
        rs.set(bake, "use_selected_to_active", False)
        rs.set(bake, "use_cage", False)
        rs.set(bake, "use_clear", True)
        rs.set(bake, "target", "IMAGE_TEXTURES")
        rs.set(bake, "margin", s.margin)
        rs.set(bake, "normal_space", "TANGENT")
        rs.set(bake, "normal_r", "POS_X")
        rs.set(bake, "normal_g", "NEG_Y" if s.normal_convention == "DIRECTX" else "POS_Y")
        rs.set(bake, "normal_b", "POS_Z")

        self._gen = run_export(context, s, jobs, out_root, rs, self._log, self._results)
        _STATE.update(running=True, progress=0.0, text="Preparing ...")
        context.window_manager.progress_begin(0, 1000)
        _redraw_all(context)
        return None

    def _end(self, context, cancelled=False):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        if self._gen is not None:
            self._gen.close()          # runs cleanup of a half-finished material
            self._gen = None
        wm.progress_end()
        try:
            context.workspace.status_text_set(None)
        except Exception:  # noqa: BLE001
            pass
        self._rs.restore()
        vl = self._view_layer
        for o in vl.objects:
            try:
                o.select_set(False)
            except RuntimeError:
                pass
        for o in self._prev_selected:
            try:
                o.select_set(True)
            except (RuntimeError, ReferenceError):
                pass
        try:
            vl.objects.active = self._prev_active
        except (RuntimeError, ReferenceError):
            pass
        _STATE.update(running=False, progress=0.0, text="")

        if self._results:
            _STATE["last_export"] = list(self._results)

        for lvl, msg in self._log:
            logger.log(getattr(logging, lvl, logging.INFO), "%s", msg)
        errors = [m for lvl, m in self._log if lvl == "ERROR"]
        warnings = [m for lvl, m in self._log if lvl == "WARNING"]
        for msg in warnings:
            self.report({"WARNING"}, msg)
        for msg in errors:
            self.report({"ERROR"}, msg)
        _redraw_all(context)

        total = sum(r["count"] for r in self._results)
        if cancelled:
            self.report({"WARNING"}, f"Export cancelled ({total} file(s) written before cancelling).")
            return {"CANCELLED"}
        if total == 0:
            if not errors and not warnings:
                self.report({"WARNING"}, "Nothing was exported (see the system console for details).")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {total} file(s) to {self._out_root}")
        return {"FINISHED"}

    # -- entry points -----------------------------------------------------------------
    def invoke(self, context, event):
        err = self._begin(context)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        try:
            if event.type == "ESC":
                return self._end(context, cancelled=True)
            if event.type != "TIMER":
                return {"RUNNING_MODAL"}
            try:
                frac, text = next(self._gen)
            except StopIteration:
                return self._end(context)
            _STATE.update(progress=frac, text=text)
            context.window_manager.progress_update(int(frac * 1000))
            try:
                context.workspace.status_text_set(f"PBR export: {text}  (Esc to cancel)")
            except Exception:  # noqa: BLE001
                pass
            _redraw_all(context)
            return {"RUNNING_MODAL"}
        except Exception as exc:  # noqa: BLE001 - never leave the scene in a modified state
            logger.exception("Mat-Baker error")
            self._log.append(("ERROR", str(exc)))
            return self._end(context, cancelled=True)

    def execute(self, context):
        """Blocking version (used when called from scripts)."""
        err = self._begin(context)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        try:
            for _ in self._gen:
                pass
        except Exception as exc:  # noqa: BLE001
            logger.exception("Mat-Baker error")
            self._log.append(("ERROR", str(exc)))
        return self._end(context)


class MATBAKER_OT_preview(Operator):
    bl_idname = "mat_baker.build_preview"
    bl_label = "Create Preview Material"
    bl_description = "Build a Principled material from the last exported files (assign it to an object to check the result)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(_STATE["last_export"])

    def execute(self, context):
        entries = _STATE["last_export"]
        if not entries:
            self.report({"ERROR"}, "No export recorded yet in this session.")
            return {"CANCELLED"}
        names = []
        for entry in entries:
            try:
                names.append(build_preview_material(entry).name)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Mat-Baker error")
                self.report({"WARNING"}, f"{entry.get('material', '?')}: {exc}")
        if not names:
            return {"CANCELLED"}
        self.report({"INFO"}, "Created material(s): " + ", ".join(names) + " - assign one to an object to preview.")
        return {"FINISHED"}


# --------------------------------------------------------------------------- #
#  Add-on preferences
# --------------------------------------------------------------------------- #

_LOG_HANDLER = None


def set_verbose(enabled):
    """Show Mat-Baker's progress messages in the system console (off by default)."""
    global _LOG_HANDLER
    if enabled and _LOG_HANDLER is None:
        _LOG_HANDLER = logging.StreamHandler()
        _LOG_HANDLER.setFormatter(logging.Formatter("[Mat-Baker] %(message)s"))
        logger.addHandler(_LOG_HANDLER)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    elif not enabled and _LOG_HANDLER is not None:
        logger.removeHandler(_LOG_HANDLER)
        _LOG_HANDLER = None
        logger.setLevel(logging.NOTSET)
        logger.propagate = True


def _on_verbose(self, context):
    set_verbose(self.verbose)


class MATBAKER_Preferences(AddonPreferences):
    bl_idname = __package__ or __name__

    verbose: BoolProperty(
        name="Verbose Console Output",
        description="Print progress details to the system console (useful when reporting a problem)",
        default=False,
        update=_on_verbose,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "verbose")
        row = layout.row()
        row.operator("wm.url_open", text="Documentation", icon=_icon("URL")).url = URL_DOCS
        row.operator("wm.url_open", text="Report an Issue", icon=_icon("URL")).url = URL_ISSUES
        layout.label(text="Mat-Baker works fully offline. It only writes to the folder you choose.")



# --------------------------------------------------------------------------- #
#  UI
# --------------------------------------------------------------------------- #

def _active_material(context):
    obj = context.active_object
    return obj.active_material if obj else None


def unavailable_hints(wanted, available):
    """Short panel hints explaining why switched-on maps cannot be produced from the material."""
    missing = {k for k in wanted if k not in available}
    hints = []
    if missing & {"basecolor", "roughness", "metallic"}:
        hints.append("Colour maps need a Principled BSDF")
    if "normal" in missing:
        hints.append("Normal: add a Bump/Displacement node")
    if missing & {"height", "ao", "curvature"}:
        hints.append("Height/AO/Curvature need a Height input")
    if missing & {"emission", "opacity"}:
        hints.append("Emission/Opacity: unused by this material")
    return hints


def draw_analysis(layout, context, s):
    box = layout.box()
    mat = _active_material(context)
    if s.scope != "ACTIVE":
        count = _cached(("jobs", s.scope), lambda: len(collect_jobs(context, s.scope)))
        box.label(text=f"{count} material(s) will be exported", icon=_icon("MATERIAL"))
    if mat is None:
        box.label(text="Select an object that has a material", icon=_icon("INFO"))
        return
    sm = _cached(("analysis", mat.as_pointer(), s.ao_method),
                 lambda: AnalysisSummary(analyze_material(mat, s.ao_method)))
    box.label(text=mat.name, icon=_icon("MATERIAL"))
    if sm.problem:
        box.label(text=sm.problem, icon=_icon("ERROR"))
        return
    if sm.warning:
        box.label(text="No top-level Principled BSDF", icon=_icon("ERROR"))
    wanted, need, can_pack = plan_export(sm, s)
    grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
    for key in MAP_ORDER:
        if key in wanted:
            ok = key in sm.available
            text = MAP_INFO[key]["label"]
            if ok and key in sm.const_keys:
                text += " (value)"
            grid.label(text=text, icon=_icon("CHECKMARK" if ok else "X"))
    for hint in unavailable_hints(wanted, sm.available):
        box.label(text=hint, icon=_icon("INFO"))
    pack = PACKS.get(s.pack_type)
    if pack:
        box.label(text=f"{pack['name']} texture", icon=_icon("CHECKMARK" if can_pack else "X"))
    files = len([k for k in wanted if k in need]) + (1 if can_pack else 0)
    box.label(text=f"{files} file(s) will be written", icon=_icon("FILE_IMAGE"))


def draw_main(layout, context):
    s = context.scene.mat_baker
    layout.use_property_split = False
    layout.use_property_decorate = False

    if _STATE["running"]:
        box = layout.box()
        box.label(text="Exporting ...", icon=_icon("TIME"))
        prog = getattr(box, "progress", None)
        if prog is not None:
            prog(factor=_STATE["progress"], type="BAR", text=f"{int(_STATE['progress'] * 100)} %")
        else:
            box.label(text=f"{int(_STATE['progress'] * 100)} %")
        box.label(text=_STATE["text"])
        box.label(text="Press Esc to cancel", icon=_icon("INFO"))
        return

    layout.row(align=True).prop(s, "ui_mode", expand=True)
    layout.separator(factor=0.5)

    layout.label(text="Preset")
    layout.prop(s, "preset", text="")
    layout.label(text="Resolution")
    layout.row(align=True).prop(s, "resolution", expand=True)
    layout.label(text="Output folder")
    layout.prop(s, "output_dir", text="")
    layout.prop(s, "scope", text="")

    draw_analysis(layout, context, s)

    row = layout.row()
    row.scale_y = 1.8
    row.operator(MATBAKER_OT_export.bl_idname, text="Export Texture Pack", icon=_icon("EXPORT"))

    if _STATE["last_export"]:
        row = layout.row(align=True)
        folder = bpy.path.abspath(s.output_dir)
        if os.path.isdir(folder):
            row.operator("wm.path_open", text="Open Folder", icon=_icon("FILE_FOLDER")).filepath = folder
        row.operator(MATBAKER_OT_preview.bl_idname, text="Preview Material", icon=_icon("SHADING_TEXTURE"))


def draw_maps(layout, context):
    s = context.scene.mat_baker
    layout.use_property_split = False
    layout.use_property_decorate = False
    grid = layout.grid_flow(row_major=True, columns=2, align=True)
    for key in MAP_ORDER:
        grid.prop(s, "map_" + key, text=MAP_INFO[key]["label"], toggle=True)
    layout.separator(factor=0.5)
    layout.label(text="Packed texture")
    layout.prop(s, "pack_type", text="")
    pack = PACKS.get(s.pack_type)
    if pack:
        layout.label(text=describe_pack(pack), icon=_icon("INFO"))


def draw_derived(layout, context):
    s = context.scene.mat_baker
    layout.use_property_split = True
    layout.use_property_decorate = False
    col = layout.column(heading="Ambient Occlusion")
    col.prop(s, "ao_method", text="Method")
    if s.ao_method == "HEIGHT":
        col.prop(s, "ao_relief", text="Relief")
        col.prop(s, "ao_radius", text="Radius")
        col.prop(s, "ao_strength", text="Strength")
    else:
        col.prop(s, "ao_samples", text="Samples")
        if s.bake_mode == "PLANE":
            col.label(text="A flat plane has no occluders:", icon=_icon("ERROR"))
            col.label(text="use \"Object's UVs\" in Bake Settings.")
    layout.separator()
    col = layout.column(heading="Curvature")
    col.prop(s, "curv_radius", text="Radius")
    col.prop(s, "curv_strength", text="Strength")
    layout.separator()
    layout.prop(s, "normalize_height")


def draw_output(layout, context):
    s = context.scene.mat_baker
    layout.use_property_split = True
    layout.use_property_decorate = False
    col = layout.column()
    col.prop(s, "file_format")
    col.prop(s, "color_depth")
    col.prop(s, "normal_convention")
    col.prop(s, "naming")
    col.prop(s, "use_subfolder")
    col.prop(s, "write_manifest")


def draw_bake(layout, context):
    s = context.scene.mat_baker
    layout.use_property_split = True
    layout.use_property_decorate = False
    col = layout.column()
    col.prop(s, "bake_mode")
    if s.bake_mode == "PLANE":
        col.prop(s, "plane_size")
    col.prop(s, "samples")
    col.prop(s, "margin")
    col.prop(s, "keep_images")


def _advanced_poll(cls, context):
    return context.scene.mat_baker.ui_mode == "ADVANCED"


def _make_draw(fn):
    def draw(self, context):
        fn(self.layout, context)
    return draw


def _make_panel_classes():
    """One main panel + four advanced sub-panels, in the 3D Viewport sidebar and the Material tab."""
    defs = [
        ("main", "Mat-Baker", None, draw_main),
        ("maps", "Maps & Packing", "main", draw_maps),
        ("derived", "AO / Curvature / Height", "main", draw_derived),
        ("output", "Output", "main", draw_output),
        ("bake", "Bake Settings", "main", draw_bake),
    ]
    spaces = (
        ("V3D", {"bl_space_type": "VIEW_3D", "bl_region_type": "UI", "bl_category": "Mat-Baker"}),
        ("MAT", {"bl_space_type": "PROPERTIES", "bl_region_type": "WINDOW", "bl_context": "material"}),
    )
    classes = []
    for tag, base in spaces:
        for key, label, parent, fn in defs:
            idname = f"MATBAKER_PT_{tag}_{key}"
            ns = dict(base)
            ns["bl_label"] = label
            ns["bl_idname"] = idname
            ns["bl_options"] = {"DEFAULT_CLOSED"} if parent else set()
            ns["draw"] = _make_draw(fn)
            if parent:
                ns["bl_parent_id"] = f"MATBAKER_PT_{tag}_{parent}"
                ns["poll"] = classmethod(_advanced_poll)
            classes.append(type(idname, (Panel,), ns))
    return classes


# --------------------------------------------------------------------------- #
#  Registration
# --------------------------------------------------------------------------- #

_PANEL_CLASSES = _make_panel_classes()
classes = (MATBAKER_Preferences, MATBAKER_Settings, MATBAKER_OT_export, MATBAKER_OT_preview) + tuple(_PANEL_CLASSES)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mat_baker = PointerProperty(type=MATBAKER_Settings)
    try:
        set_verbose(bpy.context.preferences.addons[__package__].preferences.verbose)
    except Exception:  # noqa: BLE001 - preferences not available yet: stay quiet
        pass


def unregister():
    _STATE.update(running=False, progress=0.0, text="", last_export=[])
    _UI_CACHE.clear()
    set_verbose(False)
    del bpy.types.Scene.mat_baker
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
