# Changelog

All notable changes to Mat-Baker are documented here. The project follows [Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-11-09

### Added
- **UDIM support** for *Whole Object* baking. Tick **UDIM Tiles** and Mat-Baker works out which tiles (1001, 1002, 1011, …) your UV layout uses, bakes each tile at the chosen resolution and saves one file per tile, for example `Prop_BaseColor.1001.png`, `Prop_BaseColor.1002.png`. Packed textures (ORM, Unity masks) are written per tile too.
- UDIM check in the panel: lists the tiles and warns about UVs outside the UDIM range, faces that cross a tile border, and overlaps inside a tile.
- The JSON manifest lists the UDIM tiles and uses the `<UDIM>` token in the file names.
- **Preview Material** builds UDIM images that know all their tiles.
- The bake now names the UV map explicitly, so the chosen UV map is always the one that is baked.

### Changed
- With UDIM, ambient occlusion is baked by Cycles; Curvature and *Normalize Height* are not available (they are computed from a single image's height map and would differ from tile to tile). The panel says so.
- While baking UDIM tiles Mat-Baker temporarily adds one UV map to the object and removes it afterwards; your UV maps are never changed.
- Panel: shorter labels that fit the narrow sidebar (`AO`, `*` marks fixed values), clearer hints, headings for the AO and Curvature groups.

### Fixed
- The Whole Object checklist wrongly marked baked maps such as Normal and AO as "fixed value".
- The curvature radius was displayed as `0.00`; it now shows three decimals.

## [1.0.0] - 2026-11-03

First public release.

### Added
- Bake any node-based material into PBR maps: Base Color, Normal, Roughness, Metallic, Height, Ambient Occlusion, Emission, Opacity, Curvature.
- Simple mode (preset + resolution + one button) and Advanced mode (maps, packing, AO / curvature, output, bake settings).
- Presets: Generic, Unreal Engine, Unity URP / Built-in, Unity HDRP, glTF / Godot / Three.js, Everything.
- Packed textures: ORM, Unity HDRP Mask map, Unity Metallic-Smoothness.
- Pre-flight checklist in the panel showing which maps the active material can produce, with short hints for the ones it cannot.
- Non-blocking export with progress bar; **Esc** cancels and restores the scene.
- Export scope: the active object, all selected objects, or everything in the file.
- Bake each material onto a temporary flat plane (tileable packs, one set per material), **or bake all materials of a model into one texture set** through its UV map (*Whole Object*), with UV map selection and an overlap / out-of-range warning.
- PNG / TIFF / OpenEXR output, 8 / 16 / 32-bit, OpenGL or DirectX normals, three naming styles, optional JSON manifest.
- *Preview Material* button that builds a Blender material from the exported files.
- Add-on preferences: verbose console output, links to documentation and issue tracker.

[1.2.0]: https://github.com/SANJAYA3D/Mat-Baker/releases/tag/v1.2.0
[1.1.0]: https://github.com/SANJAYA3D/Mat-Baker/releases/tag/v1.1.0
[1.0.0]: https://github.com/SANJAYA3D/Mat-Baker/releases/tag/v1.0.0
