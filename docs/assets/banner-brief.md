# Banner design brief

Target: `docs/assets/banner.png`, 1200×300 (4:1, header band) or 1400×784 (16:9, full hero — Multica's ratio). Used at the very top of the README hero.

## Visual direction

**Painterly mountain landscape with a sea of clouds**, in the same Studio Ghibli / Makoto Shinkai-adjacent digital-illustration style as Multica's hero banner. The metaphor is calm, scenic, non-technical — the product's character comes from proximity to the warm landscape, not from any depicted tech.

The specific hook: **a sea of clouds below the peaks**. Catalan *núvol* = cloud, which is the etymological seed of the wordmark "nuvel" (and the accent dot in the logo references this). Showing núvols literally in the landscape ties banner and wordmark together without anyone having to explain it.

## Style anchors

- Painterly digital illustration. Ghibli / Makoto Shinkai / Atey Ghailan / Mathias Verhasselt territory. Oil-and-light-pen feel, not vector-flat, not 3D-render.
- Atmospheric depth — multiple ranges receding into haze.
- Golden hour or just-after-sunrise light. Warm peach and gold at the horizon, cool blues in the shadows.
- Restrained — no neon, no glow effects, no lens flares, no "epic" overdrive.
- Wide breathing space. The composition is mostly sky and clouds; the peak anchors the upper third.

## Composition (recommended)

**Single figure on a high ridge, looking out over a sea of clouds; one mountain peak rising above the cloud layer; warm light at the horizon.**

- Foreground: a hiker (or just the silhouette of a person) on a rocky outcrop, viewed from behind or three-quarter back. Small in frame — the landscape dominates. Wears earthy tones (charcoal jacket, ochre pack) that echo the wordmark palette.
- Mid-ground: a sea of clouds rolling between the ridges, lit pink/peach where the sun touches them.
- Background: one or two snow-capped peaks rising clear above the clouds. Soft pink on the snow where the light hits, deep blue in the shadowed faces.
- Sky: clear gradient from deep blue at the top to warm peach at the horizon. Optional: a small crescent moon high in the blue zone (Multica has one; mirroring this is fine, omitting it is also fine).
- Light direction: low, from camera-right or behind the figure. Long shadows across the foreground rocks.

This composition reads as: "you, looking out from a vantage point, with the clouds (núvols) below."

## Color palette

| Role | Hex | Notes |
| ---- | --- | ----- |
| Sky high | `#1F3D5C` | Deep, slightly desaturated blue |
| Sky low / horizon | `#E8B58C` → `#F4D5A5` | Warm peach into pale gold at the very horizon |
| Clouds in light | `#F2C8B0` | Pink-peach where the sun touches them |
| Clouds in shadow | `#7A8AA0` | Cool blue-grey on the underside |
| Snow in light | `#F5DCC8` | Warm white, almost pink |
| Snow / rock in shadow | `#3A4A5E` | Deep cool blue |
| Foreground rock | `#6B5847` → `#8E7560` | Earthy umber |
| Figure clothing | `#2A2A28` (charcoal) + `#B6873B` (ochre accent) | Echoes the wordmark palette |

## Anti-palette (reject if present)

- Neon teal, magenta, electric blue — anything synth-wave
- Lens flares, light bloom, "god rays" overdrive
- Any visible technology in frame (drones, satellites, robots, screens, code)
- Hyper-detailed photorealism — we want painterly, not stock photo
- A second figure walking with the first (Multica's silhouette; ours is solo)
- Crisp vector flat aesthetic — wrong style entirely

## Image-gen prompts

Aspect-ratio note: Midjourney `--ar 16:9` for the hero variant or `--ar 4:1` for the slim band. Add `--style raw` if using v6+.

### Primary prompt

> A wide painterly digital illustration in the style of Studio Ghibli and Makoto Shinkai. A single solitary hiker stands on a rocky outcrop in the foreground, viewed from behind, gazing out across a vast sea of clouds. The clouds roll between distant mountain ridges and are lit warm pink and peach by the low golden-hour sun. One snow-capped mountain peak rises clear above the cloud layer in the middle distance, its snow tinted pink in the light, deep blue in shadow. The sky gradient goes from deep blue at the top to warm peach at the horizon. The hiker wears a charcoal jacket with an ochre pack — small in the frame, the landscape dominates. Atmospheric, restrained, calm. Oil-painting feel, soft brushwork. NO neon, NO lens flare, NO visible technology, NO crisp vector style.

### Variant — wider, slimmer band (for the README header)

Same prompt, end with: *"Composition centered for a 4:1 wide horizontal banner — most of the frame is sky, clouds, and distant peaks; the foreground figure sits in the lower-left quadrant; the right two-thirds is open landscape and warm sky."*

### Variant — no figure (cleaner, more abstract)

> A wide painterly digital illustration in the style of Studio Ghibli and Makoto Shinkai. A single snow-capped mountain peak rising above a sea of low pink-peach clouds at golden hour. Multiple ridge lines receding into atmospheric haze in the background. Sky gradient from deep blue at the top to warm peach at the horizon. A small crescent moon high in the blue. Painterly, restrained, calm — oil-painting feel with soft visible brushwork. No figures, no technology, no neon. Wide cinematic horizontal aspect ratio.

The no-figure variant pairs more cleanly with the wordmark — pure landscape, pure mood, no narrative competition. The figure variant is warmer and more Multica-adjacent. Either works.

## Quality bar

Reject and re-roll if:

- The peak shape reads as Mt. Everest stock-photo silhouette (too generic)
- Clouds look like cotton-ball clipart instead of soft layered atmosphere
- The figure has any uncanny anatomy issues (image gen often fails on hands; if visible, regenerate)
- Sky has banding, gradient artifacts, or a "phone wallpaper" sheen
- The piece feels overworked — too many elements, too saturated, too much detail
- Any character/text/glyphs appear in the image (image-gen often inserts garbage text)
- Feels generically "AI fantasy art" — the mark of failure is "looks like every Midjourney mountain post"

## After you have a candidate

1. Drop the file at `docs/assets/banner.png` (1400×784) — or `banner-wide.png` if you also generate the 4:1 band.
2. Uncomment the banner block at the top of `README.md`:

   ```html
   <p align="center">
     <img src="docs/assets/banner.png" alt="nuvel — production-ready agents, your way" width="100%">
   </p>
   ```

3. Push.

If the result is close but not quite right, the highest-leverage tweaks (in order): cloud color temperature → peak silhouette → light direction → figure pose. The first three are usually one-prompt-tweak each; the fourth is harder and probably means re-rolling without the figure.
