# Changelog

All notable changes to Mat-Baker are documented here. The project follows [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-03

First public release.

### Added
- Bake any node-based material into PBR maps: Base Color, Normal, Roughness, Metallic, Height, Ambient Occlusion, Emission, Opacity, Curvature.
- Simple mode (preset + resolution + one button) and Advanced mode (maps, packing, AO / curvature, output, bake settings).
- Presets: Generic, Unreal Engine, Unity URP / Built-in, Unity HDRP, glTF / Godot / Three.js, Everything.
- Packed textures: ORM, Unity HDRP Mask map, Unity Metallic-Smoothness.
- Pre-flight checklist in the panel showing which maps the active material can produce, with short hints for the ones it cannot.
- Non-blocking export with progress bar; **Esc** cancels and restores the scene.
- Export scope: active material, all materials on selected objects, or all materials in the file.
- Bake onto a temporary flat plane (tileable packs) or onto an object's own UVs.
- PNG / TIFF / OpenEXR output, 8 / 16 / 32-bit, OpenGL or DirectX normals, three naming styles, optional JSON manifest.
- *Preview Material* button that builds a Blender material from the exported files.
- Add-on preferences: verbose console output, links to documentation and issue tracker.

[1.0.0]: https://github.com/SANJAYA3D/Mat-Baker/releases/tag/v1.0.0
