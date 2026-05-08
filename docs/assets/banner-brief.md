# Banner design brief

Target: `docs/assets/banner.png`, 1200×300 (4:1), used at the very top of the README hero.

## Visual direction

Editorial / library / quiet-confident — adjacent to Multica's banner aesthetic but distinct. The wordmark direction is warm cream + charcoal italic serif (see `logo-light.svg` / `logo-dark.svg`); the banner should live in the same world.

**Anchor palette:**
- Background: cream `#F4F1EA` (or a near neighbour — `#EDE8DA`, `#F8F4EB`)
- Primary mark / type: charcoal `#2A2A28`
- Single accent (use sparingly, one element only): terracotta `#C8553D` *or* deep ochre `#B6873B`

**Anti-palette** (avoid):
- Purple gradients on white/dark — the AI-product cliché
- Cool blue/cyan tech aesthetic — too generic-startup
- Pure black on pure white — too clinical for the warmth we want
- Sans-serif anything — the wordmark is serif italic, the banner should harmonise

## Composition

Three options, in order of how Multica-adjacent they are. Pick one before generating.

### A. Pure typographic (lowest risk, most "library card")

Centered or left-aligned wordmark "nuvel" in italic serif (Fraunces / Playfair / Eames Century Modern), set large. A one-line tagline below in regular weight: *"Production-ready agents, your way."* Generous negative space. A single thin horizontal rule above or below the wordmark, in the accent color, ~1px weight.

No imagery. The banner *is* typography.

### B. Typographic + small architectural diagram

Wordmark on the left third. Right two-thirds: a small, hand-drawn-looking diagram in line weight matching the mark — a plain-English description on one side, an arrow, a small grid of three labelled boxes on the other (`adk`, `claude-agent-sdk`, `managed`). Style cue: technical-handbook line drawing, not flowchart-clipart. All in charcoal with optional terracotta accent on the arrow.

### C. Editorial illustration (highest risk, hardest to brief)

Loose, ink-and-cream illustration: an open book whose pages are dissolving into geometric shapes (squares, circles, lines) that drift to the right. Reads as "structure emerging from description." Wordmark integrated bottom-left. Inspired by hand-drawn essay illustrations (think *The New Yorker*, *Wired* feature art, NOT 3D-render-AI-stock).

## Image-gen prompt seeds

If using Midjourney / Sora / DALL-E / Imagen, paste the prompt that matches your chosen composition. Add `--ar 4:1 --style raw` (Midjourney) or equivalent aspect-ratio flag.

**Composition A:**

> A wide horizontal banner, 4:1 aspect ratio. Cream background `#F4F1EA`. Centered: the word "nuvel" in elegant italic serif typography, charcoal color `#2A2A28`, large. Below in smaller regular weight: "Production-ready agents, your way." Generous negative space. A single thin horizontal rule above the wordmark in terracotta `#C8553D`, 1px weight, 80px wide. Editorial, minimal, library-card aesthetic. No 3D, no gradients, no glow effects. Flat, vector-clean, print-quality.

**Composition B:**

> A wide horizontal banner, 4:1 aspect ratio. Cream background `#F4F1EA`. Left third: the word "nuvel" in italic serif, charcoal `#2A2A28`, large. Right two-thirds: a hand-drawn-style technical diagram in thin charcoal line. On the left of the diagram, a stylized rectangle labeled "describe what it does"; an arrow points right to a small grid of three labeled boxes — "adk", "claude-agent-sdk", "managed". The arrow is terracotta `#C8553D`. Style: technical handbook, ink-on-paper, no shading, no fills. Flat, minimal, editorial.

**Composition C:**

> A wide horizontal banner, 4:1 aspect ratio. Cream paper background with very subtle texture. Centered illustration: an open book whose pages dissolve into floating geometric shapes — clean squares, circles, thin lines — drifting toward the right edge of the frame. Charcoal ink on cream, with a single terracotta `#C8553D` accent on one of the shapes. Bottom-left, integrated into the composition: the word "nuvel" in italic serif. Style: New Yorker editorial illustration, hand-drawn ink, restrained, intelligent. No 3D, no AI-glow, no clipart aesthetic.

## Quality bar

Reject and re-roll if:
- Any character appears as gibberish or misspelled (common image-gen failure)
- The serif wordmark renders as sans
- More than one accent color appears
- Any element looks rendered, glowy, or 3D
- The composition feels overstuffed — empty space is a feature
- The illustration style reads as "AI clipart"

When you have a candidate, drop it at `docs/assets/banner.png` and uncomment the banner block at the top of `README.md`.
