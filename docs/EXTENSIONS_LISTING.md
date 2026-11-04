# Mat-Baker – release checklist for the maintainer

This file is **not** shipped inside the extension. It contains (1) a pointer to the listing texts,
(2) a checklist against the platform's Terms of Service, and (3) the publishing steps.

---

## 1. Texts

All listing and release texts (GitHub description, GitHub release note, extension description, extension release note) are in [`RELEASE_TEXTS.md`](RELEASE_TEXTS.md).

---

## 2. Terms-of-Service checklist (as of the ToS dated 10 August 2026)

| ToS | Requirement | How Mat-Baker meets it |
|---|---|---|
| 1.1 | GPL-3.0-or-later | `license = ["SPDX:GPL-3.0-or-later"]`, SPDX header in `__init__.py`, full license text in `LICENSE` |
| 1.2 | Binary assets must be CC0 | The extension zip contains **no** binary assets (only `__init__.py`, `blender_manifest.toml`, `LICENSE`). The logo files live only in the GitHub repo and are uploaded separately as listing images |
| 1.3 | Credit bundled third-party code | Nothing is bundled. NumPy is the copy that ships with Blender |
| 2.1 | No "Blender" in the name | Name is **Mat-Baker** |
| 2.2 | No Blender logo in images | The Mat-Baker logo is original artwork (tiles + ball, own palette, no orbit ring or Blender colours). Keep Blender's logo out of screenshots and thumbnails too |
| 2.3 | No implied endorsement | No such wording anywhere |
| 2.4 | Third-party trademarks | Unreal, Unity, glTF, Godot are named in text only to say which formats are supported; no logos |
| 3.1 | No surprises | Listing text above lists everything it does, including the temporary changes |
| 3.3 | Documented, maintained | README, CHANGELOG, tests – **you must commit to maintaining it** |
| 3.5 / 3.6 | Reviewable, pure Python | Plain readable Python; a test asserts there is no exec/eval/obfuscation/byte-code |
| 3.7 | No performance / stability impact | Panel analysis is cached (no per-redraw node-tree walks); export is modal; cleanup runs even on cancel/error |
| 3.9 | No tampering | Only scene render/bake settings are changed during export and then restored; user preferences are untouched; files are written only to the chosen folder |
| 3.10 | No extra requirements | Fully functional, free, no account or key |
| 4.x | Internet | No network code; no `network` permission. The two buttons in the add-on preferences only open the browser (allowed by 4.4) |
| 5.x | Self-contained | No remote code, no downloads, no dependence on other extensions |
| 6.x | No advertising | No donation / purchase / promotion text in the UI |

`python tests/test_mat_baker.py` checks the machine-checkable rows (imports, banned calls, manifest rules, GPL header, license file).

---

## 3. Before you upload – things only you can do

1. **Try it in real Blender.** The automated tests use a simulated Blender API. Export a few of your own materials with every preset and look at the results. Note the Blender versions you tested.
2. **Check the GitHub URL.** The package assumes `https://github.com/SANJAYA3D/Mat-Baker`. If your account or repo name differs, replace that string in: `mat_baker/blender_manifest.toml`, `mat_baker/__init__.py` (`URL_DOCS`, `URL_ISSUES`), `README.md`, `CHANGELOG.md`, and this file.
3. **Upload the listing images.** The upload form requires both:
   - **Icon** – 256 × 256 px PNG → `branding/mat-baker-icon-256.png`
   - **Featured image** – at least 1920 × 1080 px, 16:9 → `branding/extension-featured-image-1920x1080.png`

   Then add **real screenshots** (the panel in Simple and Advanced mode, a procedural material next to its exported maps, the Preview Material). The featured image and the README illustration are generated artwork, not screenshots. Keep the Blender logo out of every image.
4. **Validate the package with Blender:**
   `blender --command extension validate mat_baker-1.0.0.zip`
5. **Install it from disk** (`Preferences ▸ Get Extensions ▸ ▾ ▸ Install from Disk`) on a clean profile and run one export.

## 4. Publishing

**GitHub**
```bash
git init && git add . && git commit -m "Mat-Baker 1.0.0"
git branch -M main
git remote add origin https://github.com/SANJAYA3D/Mat-Baker.git
git push -u origin main
git tag v1.0.0 && git push origin v1.0.0
```
Then on GitHub: *Releases ▸ Draft a new release* → tag `v1.0.0` → attach `mat_baker-1.0.0.zip`.

**Extensions platform**
1. Sign in at https://extensions.blender.org with your Blender ID.
2. *Upload Extension* → choose `mat_baker-1.0.0.zip`. The platform reads `blender_manifest.toml`.
3. Paste the description and release note from `RELEASE_TEXTS.md` (sections 3 and 4), upload the icon, featured image and screenshots, submit. It is held for review by the moderation team.
4. For updates: raise `version` in `blender_manifest.toml`, add a CHANGELOG entry, rebuild with `python scripts/build_extension.py`, upload the new zip.
