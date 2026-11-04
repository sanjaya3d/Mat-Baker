# Mat-Baker 1.0.0 – texts to paste

Four ready-to-use texts. Everything inside the grey boxes is meant to be copied as-is.

1. [GitHub – repository description ("About")](#1-github--repository-description-about)
2. [GitHub – release note](#2-github--release-note)
3. [Blender extension – description](#3-blender-extension--description)
4. [Blender extension – release note](#4-blender-extension--release-note)

The tagline (shown in Blender's *Get Extensions* list) lives in `mat_baker/blender_manifest.toml`:
`Bake procedural materials into PBR texture packs`

---

## 1. GitHub – repository description ("About")

Repository page → ⚙ next to **About**.

**Description** (limit 350 characters)

```text
Bake procedural Blender materials into PBR texture packs in one click. Engine presets for Unreal, Unity, glTF and Godot, ORM / Mask-map packing, normal, height, AO and curvature maps, batch export. Free, offline, GPL-3.0-or-later.
```

**Website** – use the extension page once it is approved (the address is shown on the platform after approval):

```text
https://extensions.blender.org/add-ons/mat-baker/
```

**Topics**

```text
blender blender-addon blender-extension blender-python pbr texture-baking procedural-materials texture-export game-development unreal-engine unity gltf godot python
```

**Social preview** – *Settings ▸ General ▸ Social preview ▸ Upload* → `branding/github-social-preview-1280x640.png`

---

## 2. GitHub – release note

*Releases ▸ Draft a new release* → tag `v1.0.0` → target `main`.

**Release title**

```text
Mat-Baker 1.0.0 – first release
```

**Description** (Markdown)

````markdown
**Mat-Baker turns a procedural material into a PBR texture pack in one click.**
Pick an engine preset, pick a resolution, press **Export Texture Pack**.

## Highlights

- **Works with any procedural material** – colour and data channels are read from whatever node network feeds the Principled BSDF.
- **Simple mode and Advanced mode** – one button for the common case, every setting when you need it.
- **Engine presets** – Generic, Unreal Engine, Unity URP / Built-in, Unity HDRP, glTF / Godot / Three.js, Everything.
- **Nine maps** – Base Color, Normal, Roughness, Metallic, Height, Ambient Occlusion, Emission, Opacity, Curvature.
- **Packed textures** – ORM, Unity HDRP Mask map, Unity Metallic-Smoothness.
- **Pre-flight checklist** – the panel shows which maps your material can produce, with a short hint for the ones it can't, before you export.
- **Stays responsive between maps** – progress bar in the panel, **Esc** cancels and restores your scene.
- **Batch export** – active material, all materials on selected objects, or every material in the file.
- **Tileable-friendly** – bake onto a flat plane with 0–1 UVs, or onto an object's own UV map.
- **Output control** – PNG, TIFF or OpenEXR · 8 / 16 / 32-bit · OpenGL or DirectX normals · Unreal-style or generic file names · optional JSON manifest.
- **Preview Material** – builds a ready-to-assign Blender material from the exported files.
- **Clean and offline** – works on a copy of your material, removes everything it creates, restores every setting it touches, and never uses the network.

## Install

1. Download **`mat_baker-1.0.0.zip`** below. **Do not unzip it.**
2. In Blender 4.2 or newer: `Edit ▸ Preferences ▸ Get Extensions`, open the **▾** menu (top right) → **Install from Disk…** → choose the zip.
3. Open the **Mat-Baker** tab in the 3D Viewport sidebar (`N`), or `Properties ▸ Material`.

## Good to know

- Requires **Blender 4.2 or newer** with Cycles enabled (the default).
- The **Principled BSDF** must be at the top level of the material, not inside a node group.
- **Normal, Height, AO and Curvature** are made from a Displacement or Bump node with a connected *Height* input. Without one, the panel tells you why those maps are skipped.
- AO and Curvature are computed from the height map, which is ideal for flat tileable textures. A real Cycles AO bake is available for objects with geometry.
- **Preview Material** is available until you restart Blender.

## Feedback

Found a problem or have an idea? Please [open an issue](https://github.com/SANJAYA3D/Mat-Baker/issues). Turning on *Verbose Console Output* in the add-on preferences and pasting the console text makes bugs much easier to track down.

Full details are in the [README](https://github.com/SANJAYA3D/Mat-Baker#readme) and the [CHANGELOG](https://github.com/SANJAYA3D/Mat-Baker/blob/main/CHANGELOG.md).

Licensed under GPL-3.0-or-later · by **SANJAYA3D**
````

**Assets to attach:** `mat_baker-1.0.0.zip`

---

## 3. Blender extension – description

extensions.blender.org → *Upload Extension* → **Description** field (Markdown).

````markdown
**Bake procedural materials into PBR texture packs – in one click.**

Mat-Baker reads the node network that feeds your Principled BSDF and writes the finished maps to a folder you choose. Pick an engine preset, pick a resolution, press **Export Texture Pack**.

### What you get

- **Maps:** Base Color, Normal, Roughness, Metallic, Height, Ambient Occlusion, Emission, Opacity, Curvature
- **Packed textures:** ORM, Unity HDRP Mask map, Unity Metallic-Smoothness
- **Presets:** Generic, Unreal Engine, Unity URP / Built-in, Unity HDRP, glTF / Godot / Three.js, Everything
- **Formats:** PNG, TIFF, OpenEXR · 8 / 16 / 32-bit · OpenGL or DirectX normals · optional JSON manifest

### Easy by default, full control when you need it

- **Simple mode:** preset + resolution + one button.
- **Advanced mode:** choose the maps, packing, AO and curvature settings, file format, bit depth, bake target and samples.
- **Pre-flight checklist:** the panel shows which maps your material can produce – with a short hint for the ones it can't – before you export.
- **Responsive export:** progress bar between maps; **Esc** cancels (after the current map) and restores your scene.
- **Batch export:** the active material, all materials on the selected objects, or every material in the file.
- **Flexible baking:** onto a flat plane for tileable textures, or onto your object's own UV map.
- **Preview Material:** one click builds a Blender material from the exported files so you can check the result.

### How to use

1. Select an object that has your procedural material.
2. Open the **Mat-Baker** tab in the 3D Viewport sidebar (`N`) or in `Properties ▸ Material`.
3. Choose a preset, a resolution and an output folder, then click **Export Texture Pack**.

### Requirements and limits

- Blender 4.2 or newer with Cycles enabled (the default).
- The Principled BSDF must be at the top level of the material's node tree, not inside a node group.
- Normal, Height, AO and Curvature come from a Displacement or Bump node with a connected Height input.
- AO and Curvature are computed from the height map (an approximation, ideal for flat tileable textures). A real Cycles AO bake is available for objects with geometry.
- Tileability comes from your material: Mat-Baker keeps tileable input tileable, it does not make a material tileable.

### What it changes while exporting (all temporary)

- Bakes a **copy** of your material – the original is never modified.
- Adds a temporary plane, mesh, images and scene (names start with `_MATBAKER_`) and removes them again.
- Switches the render engine to Cycles and adjusts bake settings, then restores them, together with your selection.
- Creates a `<Name>_PBR` material **only** when you press *Preview Material*.

### Permissions and privacy

- **Files:** to save the baked textures (and an optional JSON file) into the folder you choose. Nothing is written anywhere else.
- No network access, no telemetry, no external dependencies.

### Links

- Documentation and source code: https://github.com/SANJAYA3D/Mat-Baker
- Report a problem: https://github.com/SANJAYA3D/Mat-Baker/issues

*Unreal Engine, Unity, glTF and Godot are trademarks or names of their respective owners and are mentioned only to describe supported formats. Mat-Baker is an independent project and is not affiliated with or endorsed by them or by the Blender Foundation.*
````

---

## 4. Blender extension – release note

Upload form (or *Version History*) → **Release notes / Changelog** field for version 1.0.0 (Markdown).

````markdown
First public release.

- Bake any node-based material into Base Color, Normal, Roughness, Metallic, Height, AO, Emission, Opacity and Curvature maps.
- Simple mode (preset + resolution + one button) and Advanced mode with every setting.
- Presets for Unreal Engine, Unity URP / HDRP and glTF / Godot / Three.js, plus a Generic and an Everything preset.
- Packed textures: ORM, Unity HDRP Mask map, Unity Metallic-Smoothness.
- Pre-flight checklist with hints, progress bar, **Esc** to cancel.
- Batch export: active material, selected objects, or all materials.
- Bake onto a flat plane (tileable) or onto an object's UVs; PNG / TIFF / OpenEXR; OpenGL or DirectX normals.
- Preview Material button.
- Add-on preferences: verbose console output, links to documentation and the issue tracker.
````

For updates, add a new section to `CHANGELOG.md`, raise `version` in `mat_baker/blender_manifest.toml`, and write a new, short note like the one above.
