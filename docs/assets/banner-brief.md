# Banner design brief

Target: `docs/assets/banner.png`, 1200×300 (4:1, header band) or 1400×784 (16:9, full hero — Multica's ratio). Used at the very top of the README hero.

## Visual direction

**Painterly mountain landscape with a sea of clouds**, in the same Studio Ghibli / Makoto Shinkai-adjacent digital-illustration style as Multica's hero banner. The metaphor is calm, scenic, non-technical — the product's character comes from proximity to the warm landscape, not from any depicted tech.

The specific hook: **Montserrat rising above a sea of clouds**. Two reinforcing references:

- *Núvol* (Catalan) = cloud, the etymological seed of the wordmark "nuvel"; the accent dot in the logo references the same diacritic. Showing núvols in the landscape ties banner and wordmark together.
- **Montserrat** is the iconic Catalan mountain near Barcelona — a cluster of rounded, eroded conglomerate rock formations whose distinctive serrated silhouette (the literal meaning of *Montserrat*) is unmistakable. Named explicitly in the prompts so image-gen locks the right shape. Morning mist between its formations is one of the most photographed views in Catalonia.

## Style anchors

- Painterly digital illustration. Ghibli / Makoto Shinkai / Atey Ghailan / Mathias Verhasselt territory. Oil-and-light-pen feel, not vector-flat, not 3D-render.
- Atmospheric depth — multiple ranges receding into haze.
- Golden hour or just-after-sunrise light. Warm peach and gold at the horizon, cool blues in the shadows.
- Restrained — no neon, no glow effects, no lens flares, no "epic" overdrive.
- Wide breathing space. The composition is mostly sky and clouds; the peak anchors the upper third.

## Composition (recommended)

**The Montserrat massif rising above a sea of low clouds at golden hour; warm light raking across the rounded rock formations.**

- Foreground (optional): a small hiker silhouette on a viewpoint, viewed from behind, looking out toward Montserrat. Small in frame — the landscape dominates. Earthy tones (charcoal jacket, ochre pack) that echo the wordmark palette. **Or omit entirely** for a cleaner abstract composition (recommended — see "no-figure variant" below).
- Mid-ground: a sea of clouds rolling at the base of the mountain, lit pink/peach where the sun touches them. Some clouds drift between the rock formations themselves.
- Background: the distinctive Montserrat silhouette — a cluster of rounded, eroded conglomerate rock formations rising vertically like fingers or stalagmites. Pink-grey rock catching the warm light, deep blue-grey in shadow. Mediterranean pines and scrub vegetation visible at the lower slopes where they emerge from the clouds.
- Sky: clear gradient from deep blue at the top to warm peach at the horizon. Optional: a thin crescent moon high in the blue zone.
- Light direction: low, raking from camera-right. Long shadows in the rock crevices, warm rim-light on the western faces of the formations.

This composition reads as: "Montserrat above the clouds (núvols)" — the wordmark's etymology, made literal in landscape.

## Color palette

| Role | Hex | Notes |
| ---- | --- | ----- |
| Sky high | `#1F3D5C` | Deep, slightly desaturated blue |
| Sky low / horizon | `#E8B58C` → `#F4D5A5` | Warm peach into pale gold at the very horizon |
| Clouds in light | `#F2C8B0` | Pink-peach where the sun touches them |
| Clouds in shadow | `#7A8AA0` | Cool blue-grey on the underside |
| Montserrat rock in light | `#D4A88A` → `#C89878` | Warm pink-grey conglomerate, sun-lit western faces |
| Montserrat rock in shadow | `#4A5260` | Deep blue-grey on the eastern crevices |
| Mediterranean vegetation | `#3A5240` → `#5A7050` | Pines and scrub on lower slopes (subtle) |
| Foreground rock / viewpoint | `#6B5847` → `#8E7560` | Earthy umber |
| Figure clothing (if used) | `#2A2A28` (charcoal) + `#B6873B` (ochre accent) | Echoes the wordmark palette |

## Anti-palette (reject if present)

- Neon teal, magenta, electric blue — anything synth-wave
- Lens flares, light bloom, "god rays" overdrive
- Any visible technology in frame (drones, satellites, robots, screens, code)
- Hyper-detailed photorealism — we want painterly, not stock photo
- A second figure walking with the first (Multica's silhouette; ours is solo)
- Crisp vector flat aesthetic — wrong style entirely

## Image-gen prompts

Aspect-ratio note: Midjourney `--ar 16:9` for the hero variant or `--ar 4:1` for the slim band. Add `--style raw` if using v6+.

### Primary prompt — no figure (recommended)

> A wide painterly digital illustration in the style of Studio Ghibli and Makoto Shinkai. The Montserrat mountain in Catalonia, Spain — its distinctive cluster of rounded, eroded conglomerate rock formations rising vertically like fingers above a sea of low clouds at golden hour. The clouds drift between the rock pillars and at the base of the mountain, lit warm pink and peach by the low sun. Pink-grey conglomerate rock catches the warm light on its western faces, deep blue-grey in shadow. Mediterranean pines and scrub vegetation visible at the lower slopes where they emerge from the clouds. Sky gradient from deep blue at the top to warm peach at the horizon. A thin crescent moon high in the blue. Atmospheric depth — multiple ridge lines receding into haze. Painterly, restrained, calm — oil-painting feel with soft visible brushwork. NO snow, NO neon, NO lens flare, NO visible technology, NO crisp vector style, NO buildings or monastery.

### Variant — with figure (more Multica-adjacent)

> A wide painterly digital illustration in the style of Studio Ghibli and Makoto Shinkai. A single solitary hiker stands on a rocky viewpoint in the foreground, viewed from behind, gazing out toward the Montserrat mountain in Catalonia. The distinctive cluster of rounded, eroded conglomerate rock formations rises vertically like fingers above a sea of low clouds at golden hour. The clouds drift between the rock pillars, lit warm pink and peach by the low sun. Pink-grey conglomerate rock in the warm light, deep blue-grey in shadow. Mediterranean pines and scrub at the lower slopes. Sky gradient from deep blue at the top to warm peach at the horizon. The hiker wears a charcoal jacket with an ochre pack — small in the frame, the landscape dominates. Atmospheric, restrained, calm. Oil-painting feel, soft brushwork. NO snow, NO neon, NO lens flare, NO visible technology, NO crisp vector style, NO buildings or monastery.

### Variant — slim band (4:1, for the README header)

Same as either prompt above, append: *"Composition centered for a 4:1 wide horizontal banner — most of the frame is sky, clouds, and the Montserrat silhouette in the right two-thirds; the left third is open peach sky and atmospheric haze."*

The no-figure variant pairs more cleanly with the wordmark — pure mood, no narrative competition. The figure variant is warmer and more Multica-adjacent. Either works; the no-figure one is more ownable.

## Quality bar

Reject and re-roll if:

- The mountain reads as a generic sharp Alpine peak instead of Montserrat's rounded, finger-like conglomerate cluster (the silhouette is the point — if it doesn't look like Montserrat, re-roll with a stronger prompt weight on "Montserrat" and "rounded conglomerate rock")
- Snow appears anywhere — Montserrat is Mediterranean, low elevation, no snow
- The Benedictine monastery / Santa Maria de Montserrat appears in the rock face — too literal, too narrative-heavy
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

If the result is close but not quite right, the highest-leverage tweaks (in order): **Montserrat silhouette accuracy** (most important — re-roll with stronger weight on "Montserrat conglomerate rock formations"), cloud color temperature, light direction, figure pose if used. The first three are usually one-prompt-tweak each; figure pose is harder and probably means re-rolling without the figure.
