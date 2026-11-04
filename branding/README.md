# Mat-Baker branding

**Concept:** four rounded tiles (the maps of a texture pack) with a flat two-tone material ball in front of them – a procedural material becoming a set of textures.
Flat colours only: no gradients, shadows or effects.

| File | Use |
|---|---|
| `mat-baker-icon.svg` | Master icon (vector) |
| `mat-baker-icon-256.png` | **extensions.blender.org icon** (256 × 256 PNG, required by the upload form) |
| `mat-baker-icon-512.png`, `mat-baker-icon-1024.png` | Larger app icon / avatar sizes |
| `mat-baker-logo-light.svg` / `.png` | Horizontal logo for **light** backgrounds (transparent) |
| `mat-baker-logo-dark.svg` / `.png` | Horizontal logo for **dark** backgrounds (transparent) |
| `github-social-preview-1280x640.png` | GitHub *Settings ▸ General ▸ Social preview* |
| `extension-featured-image-1920x1080.png` | **extensions.blender.org featured image** (16:9, required by the upload form) |
| `pbr-maps-illustration.png` | README illustration (generated artwork, not a screenshot) |
| `build_branding.py` | Regenerates everything above |

## Palette

| Name | Hex | Role |
|---|---|---|
| Slate | `#1B1F2A` | Icon tile, dark backgrounds, text on light |
| Terracotta | `#E2725B` | Base-colour tile, "Baker" in the wordmark |
| Periwinkle | `#8E8CFB` | Normal-map tile |
| Butter | `#F6D68C` | Height tile |
| Mint | `#5ED6B5` | Roughness tile |
| Paper | `#F4F6FA` | Material ball, text on dark |

## Usage

- Keep clear space around the icon of at least one quarter of its width.
- Don't recolour, stretch, rotate or add effects. Use the light logo on light backgrounds and the dark logo on dark ones.
- The mark without its tile (used inside the banners) is only meant for the slate background `#1B1F2A`.
- Never combine the Mat-Baker artwork with the Blender logo, and don't imply that Mat-Baker is made, endorsed or approved by the Blender Foundation.

## Regenerating

```bash
pip install pillow numpy fonttools
python branding/build_branding.py        # set POPPINS_DIR if the Poppins fonts are not in /usr/share/fonts/truetype/google-fonts
```

The SVGs and PNGs are generated from the same geometry, so they always match. Change the colours in `PALETTE` at the top of the script.

## Credits and license

- The wordmark is set in **Poppins Bold** (Indian Type Foundry, SIL Open Font License 1.1) and converted to outlines, so the SVG files need no font.
- The brand files in this folder are covered by the project license (GPL-3.0-or-later) unless you decide otherwise.
- They are **not** part of the extension zip; the extension package contains no binary assets.
