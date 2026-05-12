# web.py Cupola HUD Restyle — Design Spec

**Date:** 2026-05-13
**Scope:** UI/UX restyle of `pi_bridge/web.py`.
**Status:** Approved direction (Cupola HUD); pending user spec sign-off.

## Goal

Replace the current "neutral dark dashboard" styling of the Pi camera bridge web UI with a high-end space-station HUD aesthetic, inspired by IBM Carbon design language crossed with ISS Cupola / modern military HUD telemetry. Every existing feature, control, and endpoint stays. Backend untouched. The change lives entirely in the inline `INDEX_HTML` string and the `_serve_batch_index` HTML emitter inside `pi_bridge/web.py`.

## Non-goals

- No new endpoints, no new functionality, no removed features.
- No Python logic changes.
- No external JS libraries (vanilla as today).
- No build step. The file remains a single Python script with inline HTML.
- No new dependencies in `requirements.txt`.

## Affected file

`pi_bridge/web.py` only. Two regions inside it:

1. `INDEX_HTML` constant (the `/` route's HTML).
2. The HTML generated in `_Handler._serve_batch_index` (the `/batch/<name>` route).

## Visual language

### Typography

- **Family:** IBM Plex Mono everywhere. Loaded from Google Fonts (`https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&display=swap`). System mono fallback: `ui-monospace, "SF Mono", Menlo, Consolas, monospace`.
- **Weights used:** 300 (data), 400 (body), 500 (labels), 600 (active state / headings).
- **Labels** are uppercase with `letter-spacing: 0.12em`.
- **Data values** stay normal-case for legibility.

### Color tokens (CSS custom properties)

```
--bg-deep    #04060a   page background
--bg-panel   #0a0e1a   panel surfaces
--bg-raised  #0f1422   inputs, raised tiles
--border     #1c2538   default 1px stroke
--border-hi  #2a3a5c   hover/focus stroke
--ibm-blue   #4589ff   primary accent (IBM Plex blue)
--cyan       #4cc9f0   live/active accent
--amber      #ffb700   busy / transitional
--green      #38d65e   OK / success
--red        #ff5d6c   error
--text       #c8d4e8   body text
--muted      #5b6985   secondary text
--dim        #3a4459   tertiary / disabled
```

Phosphor glow (`text-shadow: 0 0 8px currentColor`) only on **active** values (live timer, REC dot, segments lit). Never on body text — would feel cheap and tank legibility.

### Panels

- 1px stroke in `--border`, hover/focus shifts to `--cyan`.
- **Chamfered corners** on top-left and bottom-right via `clip-path: polygon(...)` (~12px cut).
- HUD tab at top-left of each panel containing a panel ID, e.g. `// CAM-01 · LIVE`, `// REC-09 · CAPTURES`, `// LOG-00 · TX`.
- Subtle drop shadow: `0 4px 32px rgba(76, 201, 240, 0.04)`.
- Inner padding 12–16px depending on panel.

### Background

- `--bg-deep` page color.
- 32px dot grid at 4–5% opacity (`background-image: radial-gradient(...)`). No animation, no parallax, no starfield.

## Component map

### Header bar

A single bracketed strip across the top:

```
╱── TICALC.CAMERA ── 10.209.79.191 ── T+ HH:MM:SS ──╲
```

- "TICALC.CAMERA" wordmark in Plex Mono 600.
- Pi host (`PI_HOST` injected server-side into the HTML, currently `10.209.79.191`). Implementation note: `INDEX_HTML` becomes a small template — either a `.format()`-style string with `{pi_host}` placeholder, or assembled at request time in `do_GET` before being sent. The raw `r"""…"""` literal stays in the module but is finalized once per request.
- **Mission timer** `T+ HH:MM:SS` — client-side counter, starts on page load. Ticks every second. Glows cyan.
- A small **status caption** to the right of the timer shows the boot sequence on first load (see "Boot pulse" below), then settles to empty.
- The flanking `╱──` and `──╲` are CSS pseudo-element strokes, not literal text characters, so they render crisply at any zoom.

### Vitals strip

Four tiles below the header. Each tile has corner brackets (CSS pseudo-elements) and a status indicator:

| Tile      | Source                              | States                                                  |
| --------- | ----------------------------------- | ------------------------------------------------------- |
| `LINK`    | `state.pi.ping`                     | up=green / down=red / ?=amber                           |
| `HTTP`    | `state.pi.http`                     | ok=green / non-ok=red / ?=amber                         |
| `BUFFER`  | `state.buffer.count` / `.max`       | mini 15-segment LED row + numeric `N/15`                |
| `BATCHES` | `state.batches_saved`               | count + last batch id when present                      |

Each tile is `flex: 1` on wide screens; collapses to 2-up on narrow.

### Live panel

- Heading: `// CAM-01 · LIVE` tab.
- **Controls toolbar** above the video (replaces existing `.live-controls`):
  - Resolution select, Quality slider, Orientation select + rotate-90 button, H-flip / V-flip pill switches, Focus mode select, manual-focus diopter slider when applicable.
  - Labels uppercase muted, values cyan when active.
  - Selects/inputs use `--bg-raised` with `--border`; focus state gets a cyan inner glow.
  - H-flip / V-flip become pill toggles (checkbox-driven, but styled as sliding pills).
- **Video container** (`#live-wrap`):
  - Existing 60vh layout preserved.
  - **HUD overlay** (absolutely positioned, pointer-events: none):
    - Four corner reticles `┌ ┐ └ ┘` in cyan at the inner corners, ~24px stroke length, 1px width.
    - Faint center crosshair: two 1px lines + 6px center dot, all `--cyan` at 25% opacity.
    - Bottom-left strip: live params readout (`1080p · 15fps · q60 · AF CONT · 5.0D`).
    - Top-right: pulsing cyan "● REC" dot when `_live_on`.
- **Buffer LED bar** (replaces `.buf-bar`): 15 segments, each ~6px wide with 2px gaps. Segments illuminate progressively as `state.buffer.count` rises. Lit segments cyan; full buffer pulses softly green.
- **Primary buttons** in the title row:
  - `▶ INIT FEED` / `■ HALT FEED` — cyan-filled when streaming, outline when stopped.
  - `◉ EXECUTE CAPTURE` — cyan-filled. Disabled state amber-filled with `■ SAVING…`. Success transitions to green-filled `✓ N FRAMES` for ~1.8s then back to default. Keep existing 1800ms timeout.

### Captures panel

- Heading: `// REC-09 · CAPTURES <total>` tab.
- Each row: `▣ cap_NNN · 15 fr · 2m ago`.
- Hover: 4px slide right, border-color shifts to `--cyan`, soft cyan shadow.
- Empty state: `// NO CAPTURES — INIT FEED & EXECUTE CAPTURE` in muted text.

### TX LOG panel (activity)

- Heading: `// LOG-00 · TX` tab.
- Format per line: `T+HH:MM:SS  TAG   ▸ message`
- Source TAGs color-coded with a 2ch background pill:
  - `CAM` → cyan tint
  - `NET` → IBM-blue tint
  - `SYS` → amber tint
  - `STREAM` → green tint
- New entries fade in with a 200ms cyan flash on the background, then settle.
- Auto-scroll preserved: pin to bottom unless user has scrolled away.
- Existing 300-line cap preserved.

### Batch viewer (`/batch/<name>`)

Same palette and frame language. Replace the current minimal HTML in `_serve_batch_index` with a HUD-styled page:

- Same Plex Mono typography (loaded again, since it's a separate page) with the same system-mono fallback.
- Header strip: `╱── TICALC.CAMERA / BATCH ── cap_NNN ── 15 FRAMES ──╲`
- Back-link tab: `// ← RETURN TO BRIDGE` pointing to `/`.
- Frame grid: 140px-tall thumbnails, 1px `--border` stroke, hover → cyan stroke + slight 1.02 scale.
- Inline `<style>` only (no shared stylesheet; matches current pattern). Color tokens duplicated inline.

## Behavior

### Boot pulse

On first page load, the **header status caption** (right of the mission timer) animates briefly:

1. `// INITIALIZING TELEMETRY…` for ~400ms
2. `// LINK ESTABLISHED` for ~400ms
3. Empty string (or fades out).

Total ~800ms, runs once per page load. Panel HUD tabs (`// CAM-01 · LIVE`, `// REC-09 · CAPTURES`, `// LOG-00 · TX`) are static labels and are NOT part of the boot animation — they render at their final value from the first paint.

### Mission timer

Client-side counter starting at page load (not server uptime — page-level is simpler and what users will read). `T+ HH:MM:SS` format, updates every second via `setInterval`.

### Status indicator breathing

When a status is OK (green), its dot pulses with a 2s `cubic-bezier(0.4, 0, 0.2, 1)` opacity loop (0.55 → 1 → 0.55). When status is bad (red), no animation. When unknown (amber), faster 1s pulse to signal "transitional".

### Buffer LED progression

As `state.buffer.count` increases via SSE status updates, segments illuminate one at a time. When count reaches `state.buffer.max` (15), all segments shift to green with a soft pulse, signaling "buffer is full — capture-ready". This replaces the current single-bar `.buf-bar`.

### Transitions

All transitions 150–200ms `ease-out`. No bounce, no spring, no jitter. The aesthetic is precision instrumentation, not playful.

### Orientation transform / persistence

Existing `applyLiveTransform()` and `localStorage.ticalc.orient` persistence preserved verbatim — only the inputs' visual styling changes.

### SSE / fetch flows

Existing `/events` SSE subscription, `/api/status`, `/shots`, `/api/capture-buffer` flows all preserved. Only the rendering of each payload changes.

## Accessibility

- Color contrast: all text on `--bg-deep` and `--bg-panel` meets WCAG AA at 4.5:1. `--text` (#c8d4e8) on `--bg-deep` (#04060a) = ~14:1. `--muted` (#5b6985) on `--bg-panel` (#0a0e1a) = ~5.3:1.
- Buffer state and status colors are also conveyed via text labels (not color alone).
- Buttons retain visible focus rings (cyan outline 2px).
- `prefers-reduced-motion: reduce` disables the dot pulses, boot pulse, and capture button color transitions; keeps essential state transitions instant.

## Browser/offline considerations

- Google Fonts loaded with `display=swap`. If offline, system mono fallback renders identically in layout — only the font face changes. Acceptable for this internal tool.
- If a fully offline-safe build is required later, the IBM Plex Mono woff2 can be bundled base64 inline. Not in scope for this pass.

## Out-of-scope (explicitly deferred)

- Bundling Plex Mono offline.
- Sound effects on capture / boot.
- A separate `/admin` or `/diagnostics` page.
- Settings persistence beyond what already lives in `localStorage.ticalc.orient`.
- Keyboard shortcuts.
- Refactoring the inline-string HTML into a separate file or templating system.

## Acceptance criteria

1. Loading `http://localhost:9090/` renders the Cupola HUD layout end-to-end with the four vitals tiles, header strip, live panel, captures panel, and TX log all in place.
2. Every interaction that worked before still works: start/stop live, change resolution / quality / orientation / focus / lens, capture buffer, open batch directory, hover capture rows.
3. The page operates with `_live_on=false` (empty state) and with active streaming, with no console errors.
4. Mission timer ticks every second.
5. Buffer LED illuminates progressively as frames arrive; turns green at 15/15.
6. Visiting `/batch/<name>` renders the matching HUD-styled grid.
7. `prefers-reduced-motion: reduce` disables decorative pulses.
8. No backend changes, no new endpoints, no new Python dependencies.
9. Existing 300-line activity-log cap, 1.8s capture-button reset, SSE keepalive, localStorage orientation persistence all unchanged.
