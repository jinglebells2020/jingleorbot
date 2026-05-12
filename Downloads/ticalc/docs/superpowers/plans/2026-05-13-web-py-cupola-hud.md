# web.py Cupola HUD Restyle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the Pi camera bridge web UI in `pi_bridge/web.py` to a Cupola-HUD aesthetic — IBM Plex Mono, deep navy + IBM blue / cyan / amber, chamfered HUD panels with bracket tabs, HUD overlay on the live feed, segmented LED buffer meter, comms-style activity log — without changing any backend behavior.

**Architecture:** All edits live inside one Python file (`pi_bridge/web.py`). Two regions: the `INDEX_HTML` constant (now a `.format()` template so `PI_HOST` can be injected) and the HTML literal inside `_Handler._serve_batch_index`. No new dependencies, no new endpoints, no Python logic changes.

**Tech Stack:** Vanilla HTML / CSS / JS embedded as a Python string. Google Fonts CDN for IBM Plex Mono with system-mono fallback. Server-Sent Events + fetch flows unchanged.

**Reference spec:** `/Users/enes/Downloads/ticalc/docs/superpowers/specs/2026-05-13-web-py-cupola-hud-design.md`

**Cwd convention:** All bash commands assume cwd is `/Users/enes/Downloads/ticalc`. The git repo root is `/Users/enes` (home-as-monorepo); commit paths are relative to that root and so include the `Downloads/ticalc/...` prefix.

**Verification convention:** Each task ends with a manual browser check at `http://localhost:9090/`. To start the server during execution:

```bash
python3 /Users/enes/Downloads/ticalc/pi_bridge/web.py
```

If the Pi at `10.209.79.191` is unreachable, the page still renders (LINK and HTTP tiles will show down/error states). That's fine for visual verification — the Pi liveness is not what we're testing.

---

## File Structure

**Files modified (1):**

- `/Users/enes/Downloads/ticalc/pi_bridge/web.py` — only the `INDEX_HTML` constant (~line 230 to ~line 633) and the `_serve_batch_index` method (~line 699 to ~line 713). All other code untouched.

**Files created (0):** None.

---

## Task 1: CSS foundation, panel chrome, base typography

**Files:**
- Modify: `/Users/enes/Downloads/ticalc/pi_bridge/web.py` — `<style>` block inside `INDEX_HTML` (currently lines 235–295)

This task replaces the entire `<style>` block with the new design system foundation: color tokens, Plex Mono font, dot-grid background, panel base class with chamfered chrome + tab styling, plus styling for *existing* component classes adapted to the new tokens. Subsequent tasks will replace component-specific HTML; for now we just want a working page on the new visual foundation.

- [ ] **Step 1: Replace the `<style>` block**

In `pi_bridge/web.py`, find the existing `<style>...</style>` block (starts at line 235 with `<style>`, ends at line 295 with `</style>`). Replace its entire contents with:

```css
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&display=swap');

:root {
  --bg-deep:    #04060a;
  --bg-panel:   #0a0e1a;
  --bg-raised:  #0f1422;
  --border:     #1c2538;
  --border-hi:  #2a3a5c;
  --ibm-blue:   #4589ff;
  --cyan:       #4cc9f0;
  --amber:      #ffb700;
  --green:      #38d65e;
  --red:        #ff5d6c;
  --text:       #c8d4e8;
  --muted:      #5b6985;
  --dim:        #3a4459;
  --font-mono:  "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --panel-clip: polygon(12px 0, 100% 0, 100% calc(100% - 12px), calc(100% - 12px) 100%, 0 100%, 0 12px);
  --panel-clip-inner: polygon(11px 0, 100% 0, 100% calc(100% - 11px), calc(100% - 11px) 100%, 0 100%, 0 11px);
}

* { box-sizing: border-box; }

html, body {
  margin: 0; height: 100%;
  background: var(--bg-deep);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 400;
  font-feature-settings: "ss02", "zero";
}

body {
  display: grid;
  grid-template-rows: auto auto 1fr auto;
  gap: 12px;
  padding: 14px;
  background-image: radial-gradient(rgba(76, 201, 240, 0.06) 1px, transparent 1px);
  background-size: 32px 32px;
}

h1 { font-size: 13px; margin: 0; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase; color: var(--text); }
h2 { font-size: 10px; margin: 0; font-weight: 500; letter-spacing: 0.18em; text-transform: uppercase; color: var(--muted); }

/* ── Panel chrome ─────────────────────────────────────────────── */
.panel {
  position: relative;
  padding: 26px 14px 14px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  isolation: isolate;
}
.panel::before {
  /* border layer (full panel, clipped) */
  content: "";
  position: absolute; inset: 0;
  background: var(--border);
  clip-path: var(--panel-clip);
  z-index: -2;
  transition: background 180ms ease-out;
}
.panel::after {
  /* fill layer, 1px inset — creates the 1px stroke illusion on every edge */
  content: "";
  position: absolute; inset: 1px;
  background: var(--bg-panel);
  clip-path: var(--panel-clip-inner);
  z-index: -1;
  box-shadow: 0 4px 32px rgba(76, 201, 240, 0.04);
}
.panel:hover::before { background: var(--border-hi); }

.panel-tab {
  position: absolute;
  top: 4px;
  left: 18px;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--cyan);
  background: var(--bg-deep);
  padding: 2px 8px;
  z-index: 1;
  white-space: nowrap;
}

/* ── Buttons ──────────────────────────────────────────────────── */
button {
  background: var(--bg-raised);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 0;
  padding: 9px 14px;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  transition: border-color 150ms ease-out, color 150ms ease-out, background 150ms ease-out;
}
button:hover { border-color: var(--cyan); color: var(--cyan); }
button:focus-visible { outline: 2px solid var(--cyan); outline-offset: 2px; }
button.primary { background: var(--cyan); color: #03121b; border-color: var(--cyan); font-weight: 600; }
button.primary:hover { color: #03121b; background: #6fd6f3; }
button.capture { background: var(--cyan); color: #03121b; border-color: var(--cyan); font-weight: 600; }
button.capture:hover { color: #03121b; background: #6fd6f3; }
button.capture.busy { background: var(--amber); border-color: var(--amber); color: #1a1108; }
button.capture.done { background: var(--green); border-color: var(--green); color: #03150a; }
button:disabled { opacity: 0.65; cursor: progress; }

/* ── Layout grid ──────────────────────────────────────────────── */
.row { display: grid; grid-template-columns: 1fr 320px; gap: 12px; min-height: 0; }
@media (max-width: 900px) { .row { grid-template-columns: 1fr; } }

/* ── Existing component classes — provisional new-token styling ── */
/* (These get refined / replaced in later tasks.) */
.pills { display: flex; flex-wrap: wrap; gap: 8px; }
.pill {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0;
  padding: 5px 10px;
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
}
.pill .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }
.pill.ok .dot { background: var(--green); }
.pill.bad .dot { background: var(--red); }
.pill.unknown .dot { background: var(--amber); }
.pill b { color: var(--cyan); font-weight: 500; }

.title-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 8px; flex-wrap: wrap; }

.live-controls {
  display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
  margin-bottom: 10px;
  font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--muted);
}
.live-controls label { display: inline-flex; align-items: center; gap: 6px; }
.live-controls select,
.live-controls input[type=range] {
  background: var(--bg-raised);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 0;
  padding: 4px 7px;
  font: inherit;
  font-size: 11px;
  text-transform: none;
}
.live-controls select:focus,
.live-controls input[type=range]:focus { outline: none; border-color: var(--cyan); }
.qv { color: var(--cyan); min-width: 30px; text-align: right; font-weight: 500; }

#live-wrap {
  position: relative;
  width: 100%;
  min-height: 460px;
  height: 60vh; max-height: 75vh;
  background: #000;
  overflow: hidden;
  display: flex; align-items: center; justify-content: center;
}
#live-img { width: 100%; height: 100%; object-fit: contain; display: block;
  transform-origin: center center; transition: transform 0.15s ease; }
#live-placeholder {
  position: absolute;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.buf-meter { display: inline-flex; align-items: center; gap: 8px; }
.buf-bar { width: 80px; height: 6px; background: var(--bg-deep); border: 1px solid var(--border); overflow: hidden; }
.buf-bar > div { height: 100%; background: var(--green); transition: width 150ms ease-out; }

#log { flex: 1 1 0; min-height: 0; overflow-y: auto; font-size: 12px; padding-right: 4px; }
#log .line { padding: 1px 0; white-space: pre-wrap; word-break: break-word; }
#log .t { color: var(--muted); margin-right: 6px; }
#log .s { color: var(--muted); margin-right: 6px; }

#shots { display: grid; grid-template-columns: 1fr; gap: 6px; overflow-y: auto; flex: 1 1 0; min-height: 0; }
#shots a {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 9px;
  border: 1px solid var(--border);
  background: var(--bg-raised);
  color: var(--text);
  text-decoration: none;
  font-size: 12px;
  transition: border-color 150ms ease-out, transform 150ms ease-out;
}
#shots a:hover { border-color: var(--cyan); transform: translateX(4px); }
#shots .name { color: var(--cyan); }
#shots .meta { color: var(--muted); margin-left: auto; font-size: 11px; }
.empty { color: var(--muted); font-style: normal; padding: 4px 0; font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; }

/* ── Scrollbar polish ────────────────────────────────────────── */
*::-webkit-scrollbar { width: 6px; height: 6px; }
*::-webkit-scrollbar-track { background: transparent; }
*::-webkit-scrollbar-thumb { background: var(--border); }
*::-webkit-scrollbar-thumb:hover { background: var(--border-hi); }
</style>
```

- [ ] **Step 2: Add `class="panel"` to all three panels and add `<span class="panel-tab">` children**

The existing HTML body has three `<section class="panel">` elements (live panel, captures panel, activity panel). The class is already present. We need to add a panel-tab span as the first child of each, and ALSO remove the existing `<h2>` inside the captures and activity panels (they conflict with the tab visually).

Find the live panel `<section class="panel">` that contains `.live-controls` (currently around line 311). Immediately after the opening `<section>` tag, insert:

```html
<span class="panel-tab">// CAM-01 · LIVE</span>
```

Find the captures panel `<section class="panel">` (currently around line 359). Replace:

```html
<section class="panel">
  <h2>Recent captures <span id="batchcount" style="color: var(--muted); font-weight: normal;"></span></h2>
  <div id="shots">...</div>
</section>
```

with:

```html
<section class="panel">
  <span class="panel-tab">// REC-09 · CAPTURES <span id="batchcount" style="color: var(--muted); margin-left: 6px;"></span></span>
  <div id="shots"><div class="empty">No captures yet. Start the live view and click EXECUTE CAPTURE.</div></div>
</section>
```

Find the activity panel `<section class="panel" style="max-height: 200px;">` (currently around line 365). Replace:

```html
<section class="panel" style="max-height: 200px;">
  <h2>Activity</h2>
  <div id="log"></div>
</section>
```

with:

```html
<section class="panel" style="max-height: 200px;">
  <span class="panel-tab">// LOG-00 · TX</span>
  <div id="log"></div>
</section>
```

- [ ] **Step 3: Start the server and visually verify**

Run:

```bash
python3 /Users/enes/Downloads/ticalc/pi_bridge/web.py
```

Open `http://localhost:9090/` in a browser. Verify:

- Background is near-black with a faint cyan dot grid.
- Body and labels render in IBM Plex Mono (check that text looks like a clean geometric monospace, not the default system font).
- Each `<section class="panel">` has chamfered top-left and bottom-right corners with a 1px stroke and a small cyan tab label (`// CAM-01 · LIVE`, `// REC-09 · CAPTURES`, `// LOG-00 · TX`).
- Existing controls (resolution select, quality slider, etc.) are visible and clickable.
- No console errors in the browser devtools.

Stop the server with Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add pi_bridge/web.py
git commit -m "$(cat <<'EOF'
ticalc/web: CSS foundation for Cupola HUD restyle

Replace dashboard styling with deep-navy / IBM-blue / cyan tokens,
IBM Plex Mono throughout, chamfered HUD panel chrome with bracket
tabs, dot-grid background. Existing components carry forward with
provisional styling on the new tokens; component-specific HTML and
interactions land in subsequent commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Header bar, mission timer, PI_HOST templating, boot pulse

**Files:**
- Modify: `/Users/enes/Downloads/ticalc/pi_bridge/web.py` — `INDEX_HTML` constant + the `do_GET` handler at the `/` and `/index.html` route

The current header is the line `<h1>ticalc camera — live stream + buffer capture</h1>` plus a button row plus a `.pills` row. This task replaces the `<h1>` and its surrounding `<div class="title-row">` with a bracketed header strip, adds a client-side mission timer, runs a brief boot-pulse caption, and injects the Pi host name from the server.

- [ ] **Step 1: Convert `INDEX_HTML` to a small template + inject `PI_HOST` at request time**

Find the existing handling of `/` in `do_GET` (around line 651):

```python
if p == "/" or p == "/index.html":
    self._send(200, INDEX_HTML, "text/html; charset=utf-8")
```

Replace it with:

```python
if p == "/" or p == "/index.html":
    body = INDEX_HTML.replace("{{PI_HOST}}", PI_HOST)
    self._send(200, body, "text/html; charset=utf-8")
```

(Using a `.replace()` rather than `str.format()` so the rest of the HTML's `{...}` patterns — CSS, JS — don't need to be escaped.)

- [ ] **Step 2: Replace the header HTML in `INDEX_HTML`**

Find the existing block (currently around lines 299–308):

```html
<div>
  <div class="title-row">
    <h1>ticalc camera — live stream + buffer capture</h1>
    <div style="display:flex; gap: 8px; align-items: center;">
      <button class="primary" id="live">▶ Live view</button>
      <button class="capture" id="capture">📸 Capture last <span id="bufN">15</span></button>
    </div>
  </div>
  <div class="pills" id="status"></div>
</div>
```

Replace it with:

```html
<header class="hud-header">
  <span class="hud-cap hud-cap-l"></span>
  <span class="hud-host">{{PI_HOST}}</span>
  <span class="hud-sep">·</span>
  <h1 class="hud-title">TICALC.CAMERA</h1>
  <span class="hud-sep">·</span>
  <span class="hud-timer" id="mtimer">T+ 00:00:00</span>
  <span class="hud-status" id="bootcap"></span>
  <span class="hud-cap hud-cap-r"></span>
  <div class="hud-actions">
    <button class="primary" id="live">▶ INIT FEED</button>
    <button class="capture" id="capture">◉ EXECUTE CAPTURE <span id="bufN">15</span></button>
  </div>
</header>
<div class="pills" id="status"></div>
```

- [ ] **Step 3: Add header CSS to the `<style>` block**

Append to the `<style>` block (before the closing `</style>`):

```css
/* ── HUD header ──────────────────────────────────────────────── */
.hud-header {
  display: grid;
  grid-template-columns: 14px auto auto auto auto auto 1fr auto 14px;
  align-items: center;
  gap: 10px;
  padding: 6px 0 8px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 6px;
}
.hud-cap {
  height: 1px;
  background: var(--border);
  position: relative;
}
.hud-cap-l::before, .hud-cap-r::before {
  content: "";
  position: absolute;
  top: -6px;
  width: 12px; height: 12px;
  border-top: 1px solid var(--border);
}
.hud-cap-l::before { left: 0; transform: skewX(-45deg); transform-origin: top right; border-right: 1px solid var(--border); }
.hud-cap-r::before { right: 0; transform: skewX(45deg); transform-origin: top left; border-left: 1px solid var(--border); }
.hud-cap-l { grid-column: 1; }
.hud-cap-r { grid-column: 9; }
.hud-host { color: var(--muted); font-size: 11px; letter-spacing: 0.14em; }
.hud-sep { color: var(--dim); font-size: 12px; }
.hud-title { font-size: 14px; font-weight: 600; letter-spacing: 0.18em; color: var(--text); }
.hud-timer {
  color: var(--cyan);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.16em;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 0 8px rgba(76, 201, 240, 0.4);
}
.hud-status {
  color: var(--cyan);
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  margin-left: 14px;
  opacity: 0;
  transition: opacity 200ms ease-out;
}
.hud-status.show { opacity: 1; }
.hud-actions { grid-column: 8; display: flex; gap: 8px; }

@media (max-width: 900px) {
  .hud-header { grid-template-columns: 1fr; gap: 4px; padding: 8px; }
  .hud-cap { display: none; }
  .hud-actions { grid-column: 1; }
}
```

- [ ] **Step 4: Add mission timer + boot pulse JS**

Find the script block. Just before the line `// ── Live view ───` (currently around line 425), insert:

```javascript
// ── Mission timer ───────────────────────────────────────────────
(function startTimer() {
  const tEl = $('mtimer');
  const t0 = Date.now();
  function tick() {
    const s = Math.floor((Date.now() - t0) / 1000);
    const hh = String(Math.floor(s / 3600)).padStart(2, '0');
    const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
    const ss = String(s % 60).padStart(2, '0');
    tEl.textContent = `T+ ${hh}:${mm}:${ss}`;
  }
  tick();
  setInterval(tick, 1000);
})();

// ── Boot pulse (one-shot) ───────────────────────────────────────
(function bootPulse() {
  const cap = $('bootcap');
  cap.textContent = '// INITIALIZING TELEMETRY…';
  cap.classList.add('show');
  setTimeout(() => { cap.textContent = '// LINK ESTABLISHED'; }, 400);
  setTimeout(() => { cap.classList.remove('show'); }, 800);
  setTimeout(() => { cap.textContent = ''; }, 1100);
})();
```

- [ ] **Step 5: Update the JS that toggles the live button label**

Find `setLive` (around line 510). Replace the two `liveBtn.innerHTML = ...` lines:

```javascript
liveBtn.innerHTML = '⏹ Stop live view';
```

with

```javascript
liveBtn.innerHTML = '■ HALT FEED';
```

and

```javascript
liveBtn.innerHTML = '▶ Live view';
```

with

```javascript
liveBtn.innerHTML = '▶ INIT FEED';
```

- [ ] **Step 6: Visually verify**

Run the server, reload the page. Verify:

- The top of the page is a single thin-rule bar showing: `[diagonal cap] PI_HOST · TICALC.CAMERA · T+ 00:00:01 [diagonal cap] [INIT FEED] [EXECUTE CAPTURE 15]`.
- The mission timer increments every second.
- For ~800ms after load, the status caption shows `// INITIALIZING TELEMETRY…` then briefly `// LINK ESTABLISHED`, then fades out.
- The host name shown is the actual `PI_HOST` from `web.py` (i.e. `10.209.79.191`), confirming the templating worked.
- No console errors.

- [ ] **Step 7: Commit**

```bash
git add pi_bridge/web.py
git commit -m "$(cat <<'EOF'
ticalc/web: HUD header strip with mission timer + boot pulse

INDEX_HTML now templates PI_HOST at request time. Header replaces
the title row with a bracket-capped strip showing host, wordmark,
ticking T+ timer, and a one-shot boot caption. Primary button labels
shift to INIT FEED / EXECUTE CAPTURE.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Vitals strip (replaces pills)

**Files:**
- Modify: `/Users/enes/Downloads/ticalc/pi_bridge/web.py` — replace `.pills` HTML, replace `renderStatus()` JS, add `.vitals` CSS

This task swaps the small inline pills row for four large vital tiles with corner-bracket framing. The BUFFER tile contains a 15-segment LED mini-bar that reflects `state.buffer.count` exactly (1 segment per slot).

- [ ] **Step 1: Replace the `<div class="pills" id="status">` HTML**

Find:

```html
<div class="pills" id="status"></div>
```

(just after the `</header>` from Task 2). Replace with:

```html
<div class="vitals" id="vitals">
  <div class="vital" data-key="link">
    <span class="vital-k">Link</span>
    <span class="vital-v" id="v-link">--</span>
    <span class="vital-sub" id="v-link-sub"></span>
    <span class="vital-dot" id="v-link-dot"></span>
  </div>
  <div class="vital" data-key="http">
    <span class="vital-k">HTTP</span>
    <span class="vital-v" id="v-http">--</span>
    <span class="vital-sub" id="v-http-sub"></span>
    <span class="vital-dot" id="v-http-dot"></span>
  </div>
  <div class="vital" data-key="buffer">
    <span class="vital-k">Buffer</span>
    <span class="vital-v" id="v-buf">0/15</span>
    <span class="vital-sub" id="v-buf-sub">awaiting feed</span>
    <span class="vital-segs" id="v-buf-segs"></span>
  </div>
  <div class="vital" data-key="batches">
    <span class="vital-k">Batches</span>
    <span class="vital-v" id="v-bat">0</span>
    <span class="vital-sub" id="v-bat-sub">no saves yet</span>
    <span class="vital-dot" id="v-bat-dot"></span>
  </div>
</div>
```

- [ ] **Step 2: Add vitals CSS**

Append to the `<style>` block:

```css
/* ── Vitals strip ────────────────────────────────────────────── */
.vitals {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}
@media (max-width: 900px) {
  .vitals { grid-template-columns: repeat(2, 1fr); }
}
.vital {
  position: relative;
  padding: 14px 16px 12px;
  background: var(--bg-panel);
  --bk: var(--border);
  background-image:
    linear-gradient(to right,  var(--bk) 10px, transparent 10px) top left / 10px 1px no-repeat,
    linear-gradient(to bottom, var(--bk) 10px, transparent 10px) top left / 1px 10px no-repeat,
    linear-gradient(to left,   var(--bk) 10px, transparent 10px) top right / 10px 1px no-repeat,
    linear-gradient(to bottom, var(--bk) 10px, transparent 10px) top right / 1px 10px no-repeat,
    linear-gradient(to right,  var(--bk) 10px, transparent 10px) bottom left / 10px 1px no-repeat,
    linear-gradient(to top,    var(--bk) 10px, transparent 10px) bottom left / 1px 10px no-repeat,
    linear-gradient(to left,   var(--bk) 10px, transparent 10px) bottom right / 10px 1px no-repeat,
    linear-gradient(to top,    var(--bk) 10px, transparent 10px) bottom right / 1px 10px no-repeat;
  transition: --bk 180ms;
}
.vital.ok      { --bk: rgba(56, 214, 94, 0.55); }
.vital.bad     { --bk: rgba(255, 93, 108, 0.65); }
.vital.unknown { --bk: rgba(255, 183, 0, 0.5); }
.vital-k {
  display: block;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--muted);
}
.vital-v {
  display: block;
  margin-top: 4px;
  font-size: 18px;
  font-weight: 500;
  color: var(--text);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
}
.vital.ok      .vital-v { color: var(--green); text-shadow: 0 0 10px rgba(56, 214, 94, 0.35); }
.vital.bad     .vital-v { color: var(--red); }
.vital.unknown .vital-v { color: var(--amber); }
.vital-sub {
  display: block;
  margin-top: 2px;
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
}
.vital-dot {
  position: absolute;
  top: 14px; right: 16px;
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--muted);
  box-shadow: 0 0 0 2px var(--bg-panel);
}
.vital.ok      .vital-dot { background: var(--green); animation: breathe 2s ease-in-out infinite; }
.vital.bad     .vital-dot { background: var(--red); }
.vital.unknown .vital-dot { background: var(--amber); animation: breathe-fast 1s ease-in-out infinite; }
@keyframes breathe       { 0%, 100% { opacity: 0.55; } 50% { opacity: 1; } }
@keyframes breathe-fast  { 0%, 100% { opacity: 0.5; }  50% { opacity: 1; } }

/* Buffer mini-segments (in the BUFFER vital tile) */
.vital-segs {
  position: absolute;
  top: 16px; right: 16px;
  display: grid;
  grid-template-columns: repeat(15, 4px);
  gap: 2px;
  align-items: center;
}
.vital-segs > i {
  display: block;
  width: 4px; height: 10px;
  background: var(--dim);
}
.vital-segs > i.lit { background: var(--cyan); box-shadow: 0 0 4px rgba(76, 201, 240, 0.55); }
.vital.ok .vital-segs > i.lit { background: var(--green); box-shadow: 0 0 4px rgba(56, 214, 94, 0.55); }
```

- [ ] **Step 3: Replace `renderStatus()` JS**

Find the existing `renderStatus()` function (currently lines 388–403). Replace its entire body with:

```javascript
function renderStatus(s) {
  // ── LINK ─────────────────────────────────────────────
  const linkEl = $('v-link').parentElement;
  linkEl.classList.remove('ok', 'bad', 'unknown');
  if (s.pi.ping === 'up') {
    linkEl.classList.add('ok');
    $('v-link').textContent = 'UP';
    $('v-link-sub').textContent = 'reachable';
  } else if (s.pi.ping === '?' || s.pi.ping == null) {
    linkEl.classList.add('unknown');
    $('v-link').textContent = '--';
    $('v-link-sub').textContent = 'awaiting probe';
  } else {
    linkEl.classList.add('bad');
    $('v-link').textContent = String(s.pi.ping).toUpperCase();
    $('v-link-sub').textContent = 'no reply';
  }

  // ── HTTP ─────────────────────────────────────────────
  const httpEl = $('v-http').parentElement;
  httpEl.classList.remove('ok', 'bad', 'unknown');
  if (s.pi.http === 'ok') {
    httpEl.classList.add('ok');
    $('v-http').textContent = 'OK';
    $('v-http-sub').textContent = 'port 8080';
  } else if (s.pi.http === '?' || s.pi.http == null) {
    httpEl.classList.add('unknown');
    $('v-http').textContent = '--';
    $('v-http-sub').textContent = 'awaiting probe';
  } else {
    httpEl.classList.add('bad');
    $('v-http').textContent = String(s.pi.http).toUpperCase();
    $('v-http-sub').textContent = 'upstream fault';
  }

  // ── BUFFER ───────────────────────────────────────────
  const bufEl = $('v-buf').parentElement;
  bufEl.classList.remove('ok', 'bad', 'unknown');
  $('v-buf').textContent = `${s.buffer.count}/${s.buffer.max}`;
  if (s.buffer.count >= s.buffer.max) {
    bufEl.classList.add('ok');
    $('v-buf-sub').textContent = `armed · ${s.buffer.frames_seen} seen`;
  } else if (s.buffer.count > 0) {
    $('v-buf-sub').textContent = `filling · ${s.buffer.frames_seen} seen`;
  } else {
    bufEl.classList.add('unknown');
    $('v-buf-sub').textContent = 'awaiting feed';
  }
  renderBufSegs(s.buffer.count, s.buffer.max);

  // ── BATCHES ──────────────────────────────────────────
  const batEl = $('v-bat').parentElement;
  batEl.classList.remove('ok', 'bad', 'unknown');
  $('v-bat').textContent = String(s.batches_saved);
  if (s.batches_saved > 0) {
    batEl.classList.add('ok');
    $('v-bat-sub').textContent = s.last_batch
      ? `${s.last_batch} @ ${s.last_batch_at || ''}`
      : `${s.batches_saved} saved`;
  } else {
    $('v-bat-sub').textContent = 'no saves yet';
  }

  // ── Mirror to legacy elements (kept for now) ─────────
  $('bufN').textContent = s.buffer.count || s.buffer.max;
  $('bufcount').textContent = `${s.buffer.count}/${s.buffer.max}`;
}

function renderBufSegs(count, max) {
  const seg = $('v-buf-segs');
  if (seg.childElementCount !== max) {
    seg.innerHTML = '';
    for (let i = 0; i < max; i++) seg.appendChild(document.createElement('i'));
  }
  const kids = seg.children;
  for (let i = 0; i < kids.length; i++) {
    kids[i].classList.toggle('lit', i < count);
  }
}
```

- [ ] **Step 4: Visually verify**

Reload the page. Verify:

- The pills row is gone; in its place is a 4-tile vitals strip across the full width.
- Each tile has corner-bracket framing.
- `LINK` and `HTTP` tiles show colored states (amber while probing, green when up, red when down). The status dot in the top-right corner pulses softly when OK.
- `BUFFER` tile shows `0/15` until you start the live feed, with a 15-segment LED bar in the top-right corner. Segments light cyan as frames arrive, then shift to green when full.
- `BATCHES` shows `0` initially.
- No console errors.

- [ ] **Step 5: Commit**

```bash
git add pi_bridge/web.py
git commit -m "$(cat <<'EOF'
ticalc/web: vitals strip with segmented buffer LED

Replace the inline pills row with four corner-bracketed vital tiles
(LINK, HTTP, BUFFER, BATCHES). BUFFER tile carries a 15-segment LED
mini-bar that reflects state.buffer.count slot-by-slot, cyan while
filling, green when armed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Live panel HUD overlay + pill toggles + segmented buffer in controls

**Files:**
- Modify: `/Users/enes/Downloads/ticalc/pi_bridge/web.py` — `INDEX_HTML` live-panel HTML + CSS + a small JS update

This task adds the HUD overlay layer on top of the live video (corner reticles, center crosshair, REC dot, live params readout), replaces the inline `.buf-bar` with a 15-segment LED bar in the controls strip, and converts H-flip / V-flip checkboxes into styled pill toggles.

- [ ] **Step 1: Replace the `#live-wrap` block**

Find:

```html
<div id="live-wrap">
  <img id="live-img" alt="live view">
  <span id="live-placeholder">click ▶ Live view to start streaming</span>
</div>
```

Replace with:

```html
<div id="live-wrap">
  <img id="live-img" alt="live view">
  <span id="live-placeholder">// AWAITING FEED — PRESS INIT FEED</span>
  <div class="hud-overlay" aria-hidden="true">
    <span class="reticle r-tl"></span>
    <span class="reticle r-tr"></span>
    <span class="reticle r-bl"></span>
    <span class="reticle r-br"></span>
    <span class="crosshair"></span>
    <span class="rec-dot" id="recdot"></span>
    <span class="hud-params" id="hudparams">--</span>
  </div>
</div>
```

- [ ] **Step 2: Replace the buffer meter inline in the controls strip with a segmented LED row**

Find the existing `.buf-meter` span (around line 349):

```html
<span class="buf-meter">Buffer
  <span class="buf-bar"><div id="bufbar" style="width:0%"></div></span>
  <span id="bufcount" style="color: var(--cyan);">0/15</span>
</span>
```

Replace with:

```html
<span class="buf-meter">Buffer
  <span class="buf-leds" id="bufleds"></span>
  <span id="bufcount">0/15</span>
</span>
```

- [ ] **Step 3: Add HUD overlay + pill toggle + buffer-LED CSS**

Append to the `<style>` block:

```css
/* ── HUD overlay on live video ───────────────────────────────── */
.hud-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 2;
}
.reticle {
  position: absolute;
  width: 22px; height: 22px;
  border-color: var(--cyan);
  border-style: solid;
  opacity: 0.7;
}
.r-tl { top: 10px;    left: 10px;    border-width: 1px 0 0 1px; }
.r-tr { top: 10px;    right: 10px;   border-width: 1px 1px 0 0; }
.r-bl { bottom: 10px; left: 10px;    border-width: 0 0 1px 1px; }
.r-br { bottom: 10px; right: 10px;   border-width: 0 1px 1px 0; }
.crosshair {
  position: absolute;
  top: 50%; left: 50%;
  width: 28px; height: 28px;
  transform: translate(-50%, -50%);
  opacity: 0.35;
}
.crosshair::before, .crosshair::after {
  content: "";
  position: absolute;
  background: var(--cyan);
}
.crosshair::before { top: 50%; left: 0; right: 0; height: 1px; transform: translateY(-50%); }
.crosshair::after  { left: 50%; top: 0; bottom: 0; width: 1px; transform: translateX(-50%); }
.crosshair > i {} /* unused; reserved for center dot if we later inject */
.rec-dot {
  position: absolute;
  top: 14px; right: 18px;
  display: none;
  align-items: center;
  gap: 7px;
  color: var(--cyan);
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}
.rec-dot.on { display: inline-flex; }
.rec-dot::before {
  content: "";
  display: inline-block;
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--cyan);
  box-shadow: 0 0 6px rgba(76, 201, 240, 0.75);
  animation: rec-pulse 1.2s ease-in-out infinite;
}
.rec-dot::after {
  content: "REC";
}
@keyframes rec-pulse { 0%, 100% { opacity: 0.4; } 50% { opacity: 1; } }
.hud-params {
  position: absolute;
  bottom: 12px; left: 14px;
  color: var(--cyan);
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  background: rgba(4, 6, 10, 0.6);
  padding: 3px 8px;
  border: 1px solid rgba(76, 201, 240, 0.25);
}

/* Override placeholder above the overlay */
#live-placeholder { z-index: 3; }
#live-img { z-index: 1; }

/* ── Pill toggles (H-flip / V-flip) ──────────────────────────── */
.live-controls input[type=checkbox] { display: none; }
.live-controls .pill-tog {
  display: inline-flex; align-items: center; gap: 6px;
  cursor: pointer; user-select: none;
}
.live-controls .pill-tog .pill-slot {
  display: inline-flex;
  width: 30px; height: 16px;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  position: relative;
  transition: background 150ms, border-color 150ms;
}
.live-controls .pill-tog .pill-slot::before {
  content: "";
  position: absolute;
  top: 1px; left: 1px;
  width: 12px; height: 12px;
  background: var(--muted);
  transition: left 150ms, background 150ms;
}
.live-controls .pill-tog input:checked + .pill-slot {
  background: rgba(76, 201, 240, 0.18);
  border-color: var(--cyan);
}
.live-controls .pill-tog input:checked + .pill-slot::before {
  left: 15px;
  background: var(--cyan);
  box-shadow: 0 0 6px rgba(76, 201, 240, 0.5);
}

/* ── Buffer LED row (in controls strip) ──────────────────────── */
.buf-leds {
  display: inline-grid;
  grid-template-columns: repeat(15, 6px);
  gap: 2px;
  align-items: center;
}
.buf-leds > i {
  display: block;
  width: 6px; height: 12px;
  background: var(--dim);
}
.buf-leds > i.lit { background: var(--cyan); box-shadow: 0 0 4px rgba(76, 201, 240, 0.6); }
.buf-leds.full > i.lit { background: var(--green); box-shadow: 0 0 6px rgba(56, 214, 94, 0.55); animation: breathe 2s ease-in-out infinite; }
#bufcount { color: var(--cyan); font-weight: 500; font-variant-numeric: tabular-nums; }
.buf-meter { font-size: 10px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--muted); }
```

- [ ] **Step 4: Rewrite the H-flip / V-flip checkboxes as pill toggles in the HTML**

Find:

```html
<label style="margin-left: 6px;"><input type="checkbox" id="hflip"> H-flip</label>
<label><input type="checkbox" id="vflip"> V-flip</label>
```

Replace with:

```html
<label class="pill-tog" style="margin-left: 6px;"><input type="checkbox" id="hflip"><span class="pill-slot"></span>H-Flip</label>
<label class="pill-tog"><input type="checkbox" id="vflip"><span class="pill-slot"></span>V-Flip</label>
```

- [ ] **Step 5: Add JS to populate the buffer LED row, REC dot, and HUD params readout**

In the script block, just below `renderBufSegs()` from Task 3 (or right after the existing `renderStatus()` if you placed it there), add:

```javascript
// Buffer LED row (in the live-controls strip)
function renderBufLeds(count, max) {
  const row = $('bufleds');
  if (!row) return;
  if (row.childElementCount !== max) {
    row.innerHTML = '';
    for (let i = 0; i < max; i++) row.appendChild(document.createElement('i'));
  }
  for (let i = 0; i < row.children.length; i++) {
    row.children[i].classList.toggle('lit', i < count);
  }
  row.classList.toggle('full', count >= max);
}

// Hud params readout (live res + fps + quality + AF mode + diopter)
function updateHudParams() {
  const params = $('hudparams');
  const res = RES_LABELS[resselect.value] || resselect.value;
  const af = afselect.value === 'manual'
    ? `AF MAN · ${diopterLabel(lensSlider.value)}`
    : (afselect.value === 'continuous' ? 'AF CONT' : 'AF AUTO');
  const rot = parseInt(rotselect.value, 10) || 0;
  const flips = (hflipCb.checked ? ' Hx' : '') + (vflipCb.checked ? ' Vx' : '');
  params.textContent = `${res} · Q${_q} · ${af} · ROT${rot}°${flips}`;
}

// REC dot visibility (toggled by setLive)
function setRecDot(on) { $('recdot').classList.toggle('on', !!on); }
```

- [ ] **Step 6: Wire those helpers into existing render + setLive flows**

In `renderStatus()` from Task 3, just before the closing `}` of the function (right after the `$('bufcount').textContent = ...` line), add:

```javascript
renderBufLeds(s.buffer.count, s.buffer.max);
```

In `setLive(on)` (around line 510), inside the `if (on)` branch, after `liveImg.src = streamUrl();`, add:

```javascript
setRecDot(true);
updateHudParams();
```

And in the `else` branch, after `livePlaceholder.textContent = '...'`, add:

```javascript
setRecDot(false);
```

Wire `updateHudParams()` into the existing change handlers. Some of the originals are one-line arrow expressions and need conversion to block bodies.

Replace:

```javascript
rotselect.addEventListener('change', () => applyOrientation(true));
hflipCb.addEventListener('change',  () => applyOrientation(true));
vflipCb.addEventListener('change',  () => applyOrientation(true));
```

with:

```javascript
rotselect.addEventListener('change', () => { applyOrientation(true); updateHudParams(); });
hflipCb.addEventListener('change',  () => { applyOrientation(true); updateHudParams(); });
vflipCb.addEventListener('change',  () => { applyOrientation(true); updateHudParams(); });
```

For the listeners that are already block-bodied, just add `updateHudParams();` at the end of the block:

- `qslider.addEventListener('change', () => { ... })` (currently around line 537) — append `updateHudParams();`.
- `resselect.addEventListener('change', () => { ... })` (currently around line 541) — append `updateHudParams();`.
- `afselect.addEventListener('change', () => { ... })` (currently around line 565) — append `updateHudParams();`.
- `lensSlider.addEventListener('input', () => { updateLensLabel(); })` (currently around line 575) — change to `lensSlider.addEventListener('input', () => { updateLensLabel(); updateHudParams(); });`.

(The `lensSlider.addEventListener('change', ...)` block-body handler also exists but doesn't need an update — it already restarts the stream and the hud params get recomputed on restart-driven UI changes elsewhere; adding it here would be redundant.)

Then, just below the existing `updateLensLabel(); updateAfMode();` (around line 483), append:

```javascript
updateHudParams();
```

so the params readout starts populated.

- [ ] **Step 7: Update the capture-button click handler to toggle `.busy` / `.done` state classes**

Find the existing `captureBtn.addEventListener('click', ...)` (currently around lines 593–618). Replace its entire body with:

```javascript
captureBtn.addEventListener('click', async () => {
  if (captureBtn.disabled) return;
  const orig = captureBtn.innerHTML;
  captureBtn.disabled = true;
  captureBtn.classList.add('busy');
  captureBtn.classList.remove('done');
  captureBtn.innerHTML = '■ SAVING…';
  try {
    const rot = parseInt(rotselect.value, 10) || 0;
    const hf  = hflipCb.checked ? 1 : 0;
    const vf  = vflipCb.checked ? 1 : 0;
    const r = await fetch(`/api/capture-buffer?rot=${rot}&hflip=${hf}&vflip=${vf}`, { method: 'POST' });
    if (!r.ok) {
      const txt = await r.text();
      appendLog({ t: '', src: 'sys', msg: `capture failed (${r.status}): ${txt}` });
      captureBtn.classList.remove('busy');
      captureBtn.innerHTML = r.status === 409 ? '⚠ BUFFER EMPTY' : '⚠ FAILED';
    } else {
      const o = await r.json();
      captureBtn.classList.remove('busy');
      captureBtn.classList.add('done');
      captureBtn.innerHTML = `✓ ${o.saved} FRAMES`;
      refreshShots();
    }
  } catch (e) {
    appendLog({ t: '', src: 'sys', msg: `capture error: ${e}` });
    captureBtn.classList.remove('busy');
    captureBtn.innerHTML = '⚠ FAILED';
  } finally {
    setTimeout(() => {
      captureBtn.disabled = false;
      captureBtn.classList.remove('busy', 'done');
      captureBtn.innerHTML = orig;
    }, 1800);
  }
});
```

- [ ] **Step 8: Visually verify**

Reload, then click `▶ INIT FEED`. Verify:

- The live video appears with four cyan corner reticles, a faint center crosshair, and a pulsing cyan `● REC` indicator in the top-right.
- The bottom-left of the video frame shows live params: `1920×1080 @ ~15FPS · Q60 · AF CONT · ROT0°` (or similar).
- The buffer LED row in the controls strip illuminates progressively as frames arrive; turns green and breathes when full.
- H-flip and V-flip render as pill toggles, slide cyan when active, and still affect the live view.
- Changing resolution / quality / orientation / focus updates the bottom-left HUD params readout.
- Stop the feed — the REC dot disappears and the placeholder reads `// AWAITING FEED — PRESS INIT FEED`.
- No console errors.

- [ ] **Step 9: Commit**

```bash
git add pi_bridge/web.py
git commit -m "$(cat <<'EOF'
ticalc/web: HUD overlay on live feed + pill flip toggles + LED buffer

Live video gains corner reticles, faint center crosshair, pulsing REC
indicator, and a bottom-left params readout (res · quality · AF mode ·
rotation · flips). Buffer meter swaps the gradient bar for a 15-slot
LED row that pulses green when full. H-flip / V-flip checkboxes
restyled as sliding pill toggles.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Captures panel + TX LOG comms styling

**Files:**
- Modify: `/Users/enes/Downloads/ticalc/pi_bridge/web.py` — `#shots`, `#log` CSS and the `refreshShots()` + `appendLog()` JS

This task replaces the captures rows with bracketed entries, adds source-tag color pills to the activity log, and gives new log entries a brief cyan fade-in.

- [ ] **Step 1: Replace captures CSS**

In the `<style>` block, find the block starting with `#shots a { display: flex; ...}` (added in Task 1) and replace the entire `#shots` cluster with:

```css
#shots {
  display: grid;
  grid-template-columns: 1fr;
  gap: 6px;
  overflow-y: auto;
  flex: 1 1 0;
  min-height: 0;
}
#shots a {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  background: var(--bg-raised);
  color: var(--text);
  text-decoration: none;
  font-size: 12px;
  transition: border-color 150ms ease-out, transform 150ms ease-out, background 150ms ease-out;
}
#shots a::before {
  content: "▣";
  color: var(--cyan);
  font-size: 12px;
}
#shots a:hover { border-color: var(--cyan); transform: translateX(4px); background: rgba(76, 201, 240, 0.06); }
#shots .name { color: var(--cyan); font-weight: 500; letter-spacing: 0.04em; }
#shots .meta {
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  white-space: nowrap;
}
```

- [ ] **Step 2: Replace TX LOG CSS**

Find the `#log` block from Task 1. Replace it with:

```css
#log {
  flex: 1 1 0;
  min-height: 0;
  overflow-y: auto;
  font-size: 11px;
  padding-right: 4px;
  letter-spacing: 0.04em;
}
#log .line {
  display: grid;
  grid-template-columns: auto auto 1fr;
  align-items: baseline;
  gap: 10px;
  padding: 2px 0;
  white-space: pre-wrap;
  word-break: break-word;
  border-left: 2px solid transparent;
  padding-left: 8px;
  margin-left: -8px;
  transition: border-color 600ms ease-out, background 600ms ease-out;
}
#log .line.fresh {
  border-left-color: var(--cyan);
  background: rgba(76, 201, 240, 0.06);
}
#log .t {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  font-size: 10px;
  letter-spacing: 0.12em;
}
#log .s {
  display: inline-block;
  padding: 1px 6px;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  background: rgba(255, 255, 255, 0.04);
  color: var(--muted);
  min-width: 50px;
  text-align: center;
}
#log .s.cam    { color: var(--cyan);     background: rgba(76, 201, 240, 0.10); }
#log .s.net    { color: var(--ibm-blue); background: rgba(69, 137, 255, 0.10); }
#log .s.sys    { color: var(--amber);    background: rgba(255, 183, 0,  0.10); }
#log .s.stream { color: var(--green);    background: rgba(56, 214, 94,  0.10); }
#log .m { color: var(--text); }
#log .m::before { content: "▸ "; color: var(--muted); }
```

- [ ] **Step 3: Replace `appendLog()` JS to emit the new structure**

Find `appendLog()` (currently around lines 378–386). Replace its entire body with:

```javascript
function appendLog(ev) {
  const wasNearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  const div = document.createElement('div');
  const srcKey = String(ev.src || '').toLowerCase();
  const srcClass = ['cam', 'net', 'sys', 'stream'].includes(srcKey) ? srcKey : '';
  div.className = 'line fresh';
  div.innerHTML =
    `<span class="t">${esc(ev.t)}</span>` +
    `<span class="s ${srcClass}">${esc(ev.src)}</span>` +
    `<span class="m">${esc(ev.msg)}</span>`;
  log.appendChild(div);
  setTimeout(() => div.classList.remove('fresh'), 700);
  while (log.childElementCount > 300) log.removeChild(log.firstChild);
  if (wasNearBottom) log.scrollTop = log.scrollHeight;
}
```

- [ ] **Step 4: Replace `refreshShots()` JS to use the new row structure**

Find `refreshShots()` (currently around lines 405–421). Replace the inner `shots.innerHTML = ...` line with:

```javascript
shots.innerHTML = list.slice(0, 30).map(b =>
  `<a href="/batch/${encodeURIComponent(b.name)}" target="_blank">
     <span class="name">${esc(b.name)}</span>
     <span class="meta">${b.frames} fr · ${b.ago}</span>
   </a>`
).join('');
```

Also update the empty state inside `refreshShots()`:

```javascript
shots.innerHTML = '<div class="empty">// NO CAPTURES — INIT FEED &amp; EXECUTE CAPTURE</div>';
```

- [ ] **Step 5: Visually verify**

Reload. Verify:

- Activity log entries show `[HH:MM:SS] [SRC] ▸ message`, with the source tag in a small color-coded pill (cyan for CAM, blue for NET, amber for SYS, green for STREAM).
- New log entries flash with a brief cyan-tinted background that fades out over ~700ms.
- Captures panel rows render as `▣  cap_NNN     15 fr · 2m ago`; hovering slides the row 4px right and outlines it cyan.
- If no captures exist, the empty state reads `// NO CAPTURES — INIT FEED & EXECUTE CAPTURE`.
- No console errors.

- [ ] **Step 6: Commit**

```bash
git add pi_bridge/web.py
git commit -m "$(cat <<'EOF'
ticalc/web: comms-style TX log + bracketed capture rows

Activity log entries gain color-coded source pills (CAM cyan, NET
blue, SYS amber, STREAM green) and a 700ms cyan flash on arrival.
Capture rows pick up a leading ▣ glyph, slide-right hover, and a
short metadata strip in muted small caps.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Batch viewer page (`/batch/<name>`)

**Files:**
- Modify: `/Users/enes/Downloads/ticalc/pi_bridge/web.py` — `_Handler._serve_batch_index` method (currently lines 699–713)

This task gives the per-batch viewer the same HUD aesthetic so the experience is consistent when the user clicks through to a capture.

- [ ] **Step 1: Replace `_serve_batch_index`**

Find the existing method:

```python
def _serve_batch_index(self, name):
    d = SAVE_DIR / Path(name).name
    if not d.is_dir():
        self._send(404, "no such batch"); return
    frames = sorted(d.glob("frame_*.jpg"))
    items = "".join(
        f'<a href="/batchfile/{name}/{f.name}" target="_blank" style="display:inline-block; margin: 4px;">'
        f'<img src="/batchfile/{name}/{f.name}" style="height:140px; border-radius:6px; border: 1px solid #262a32;"></a>'
        for f in frames
    )
    html = (f"<!doctype html><html><body style='background:#0e0f12;color:#d6d9df;font-family:system-ui;padding:14px'>"
            f"<h2 style='font-weight:500'>{name}</h2>"
            f"<p style='color:#7d8390'>{len(frames)} frames</p>"
            f"<div>{items}</div></body></html>")
    self._send(200, html, "text/html; charset=utf-8")
```

Replace with:

```python
def _serve_batch_index(self, name):
    d = SAVE_DIR / Path(name).name
    if not d.is_dir():
        self._send(404, "no such batch"); return
    frames = sorted(d.glob("frame_*.jpg"))
    safe_name = name.replace("<", "&lt;").replace(">", "&gt;")
    items = "".join(
        f'<a class="thumb" href="/batchfile/{name}/{f.name}" target="_blank">'
        f'<img src="/batchfile/{name}/{f.name}" alt="{f.name}"></a>'
        for f in frames
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{safe_name} — ticalc batch</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&display=swap');
:root {{
  --bg-deep: #04060a; --bg-panel: #0a0e1a; --bg-raised: #0f1422;
  --border: #1c2538; --border-hi: #2a3a5c;
  --cyan: #4cc9f0; --text: #c8d4e8; --muted: #5b6985; --dim: #3a4459;
  --font-mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; background: var(--bg-deep); color: var(--text); font-family: var(--font-mono); font-size: 13px; }}
body {{ padding: 14px; background-image: radial-gradient(rgba(76,201,240,0.06) 1px, transparent 1px); background-size: 32px 32px; min-height: 100vh; }}
.hud-header {{
  display: flex; align-items: center; gap: 10px;
  padding: 6px 14px 8px; border-bottom: 1px solid var(--border); margin-bottom: 14px;
}}
.hud-title {{ font-size: 14px; font-weight: 600; letter-spacing: 0.18em; }}
.hud-sub   {{ color: var(--muted); font-size: 11px; letter-spacing: 0.14em; }}
.hud-sep   {{ color: var(--dim); font-size: 12px; }}
.back-tab {{
  color: var(--cyan); text-decoration: none;
  border: 1px solid var(--border); padding: 4px 10px;
  font-size: 10px; letter-spacing: 0.18em; text-transform: uppercase;
  transition: border-color 150ms ease-out, background 150ms ease-out;
}}
.back-tab:hover {{ border-color: var(--cyan); background: rgba(76,201,240,0.06); }}
.frames {{
  display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 10px;
}}
.thumb {{
  display: block;
  border: 1px solid var(--border);
  background: #000;
  transition: border-color 150ms ease-out, transform 150ms ease-out;
}}
.thumb img {{
  display: block;
  width: 100%; height: 140px;
  object-fit: cover;
}}
.thumb:hover {{ border-color: var(--cyan); transform: scale(1.02); }}
.empty {{ color: var(--muted); padding: 16px 0; letter-spacing: 0.14em; text-transform: uppercase; font-size: 11px; }}
</style>
</head>
<body>
  <header class="hud-header">
    <a class="back-tab" href="/">// ← RETURN TO BRIDGE</a>
    <span class="hud-sep">·</span>
    <span class="hud-sub">TICALC.CAMERA / BATCH</span>
    <span class="hud-sep">·</span>
    <span class="hud-title">{safe_name}</span>
    <span class="hud-sep">·</span>
    <span class="hud-sub">{len(frames)} FRAMES</span>
  </header>
  <div class="frames">
    {items or '<div class="empty">// no frames in this batch</div>'}
  </div>
</body>
</html>"""
    self._send(200, html, "text/html; charset=utf-8")
```

- [ ] **Step 2: Visually verify**

Run the server, capture a buffer (or use an existing batch). On the main page, click a capture row. Verify:

- The batch page opens in a new tab using the same dark navy + Plex Mono palette and dot-grid background.
- A header strip shows `[// ← RETURN TO BRIDGE] · TICALC.CAMERA / BATCH · cap_NNN · 15 FRAMES`.
- Frames render in a responsive grid with 1px borders; hover scales each thumb slightly and outlines it cyan.
- Clicking the back tab returns to `/`.
- No console errors.

- [ ] **Step 3: Commit**

```bash
git add pi_bridge/web.py
git commit -m "$(cat <<'EOF'
ticalc/web: HUD-styled batch viewer page

Replace the minimal /batch/<name> page with a matching Cupola HUD
layout: Plex Mono, dark navy + cyan, header strip with return tab,
responsive frame grid with subtle hover scaling.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Reduced motion, final cleanup, full smoke test

**Files:**
- Modify: `/Users/enes/Downloads/ticalc/pi_bridge/web.py` — append `prefers-reduced-motion` rules to `<style>` and a small cleanup pass

- [ ] **Step 1: Append reduced-motion overrides**

Append to the `<style>` block (just before `</style>`):

```css
/* ── Reduced motion ──────────────────────────────────────────── */
@media (prefers-reduced-motion: reduce) {
  .vital-dot,
  .buf-leds.full > i.lit,
  .vital-segs > i.lit {
    animation: none !important;
  }
  .rec-dot::before { animation: none !important; opacity: 1 !important; }
  .hud-status { transition: none !important; }
  #log .line { transition: none !important; }
  #shots a, button, .panel, .panel::before { transition: none !important; }
}
```

- [ ] **Step 2: Sanity-check the JS — verify nothing references removed elements**

Search the script block for these legacy IDs (which we removed or repurposed in earlier tasks): `bufbar`, `batchcount`. Confirm:

- `bufbar` — should NOT appear in the JS anymore. If you see it inside the old `renderStatus()` you missed, that block should already have been replaced in Task 3. Grep:

```bash
grep -n "bufbar" /Users/enes/Downloads/ticalc/pi_bridge/web.py
```

Expected: zero matches.

- `batchcount` — still referenced in `refreshShots()` (the `$('batchcount').textContent = ...` line). The element now lives inside the captures panel-tab; it's still in the DOM, so this still works. No action needed.

If the `bufbar` grep returns matches, remove those lines (they'd cause `Cannot read properties of null` errors when the page first renders).

- [ ] **Step 3: Full smoke test**

Start the server. Open `http://localhost:9090/`. Run through every interaction:

1. **Page loads cleanly.** Header strip, vitals, panels, log all visible. Boot caption flashes for ~800ms then fades. No console errors.
2. **Mission timer ticks** every second.
3. **`▶ INIT FEED`** — feed starts; REC dot pulses; live params readout populates; vitals BUFFER tile fills; LED row in controls fills cyan; turns green at 15/15.
4. **Change resolution** (every option). Feed restarts; HUD params readout updates.
5. **Adjust quality slider.** Releasing the slider triggers a restart; HUD params readout updates.
6. **Toggle H-flip / V-flip.** Pill toggles slide cyan; live image flips; HUD params readout shows ` Hx Vx` suffix.
7. **Cycle orientation** (0°, 90°, 180°, 270°) via the dropdown and the rotate-90 button. Image rotates correctly; HUD params reflect rotation.
8. **Focus mode** — switch among Continuous / Auto / Manual. Manual reveals the diopter slider; HUD params show `AF MAN · 5.0D · 20cm`.
9. **`◉ EXECUTE CAPTURE`.** Button turns amber briefly (`■ SAVING…`), then green (`✓ N FRAMES`), then back. Captures panel gains a new row at the top.
10. **Click a capture row.** Batch page opens in a new tab; HUD-styled grid renders. Click `// ← RETURN TO BRIDGE` — back on `/`.
11. **`■ HALT FEED`.** Feed stops; REC dot disappears; placeholder shows `// AWAITING FEED — PRESS INIT FEED`.
12. **Reduced motion** — temporarily enable "reduce motion" in your OS settings (macOS: System Settings → Accessibility → Display → Reduce motion). Reload. Verify status dots no longer pulse, REC dot stays solid (no pulse), log entries don't fade.

If everything passes, proceed. If anything fails, fix it inline, retest, then commit.

- [ ] **Step 4: Final commit (only if Step 1 or fixes from Step 2/3 produced changes)**

```bash
git add pi_bridge/web.py
git commit -m "$(cat <<'EOF'
ticalc/web: prefers-reduced-motion overrides + cleanup

Disable decorative animations (dot pulses, REC pulse, log fade,
button transitions) under reduce-motion. Final pass after smoke test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If no changes resulted (smoke test all passed and reduced-motion CSS is the only addition), this commit still lands the reduced-motion rules.

---

## Self-Review Checklist (after writing, before handing off)

1. **Spec coverage**
   - Visual language tokens — Task 1.
   - Plex Mono font + fallback — Task 1.
   - Panel chrome (chamfer + tab) — Task 1.
   - Dot grid background — Task 1.
   - Header strip + mission timer + boot pulse + PI_HOST templating — Task 2.
   - Vitals strip with mini buffer LED — Task 3.
   - Status indicator breathing — Task 3.
   - Live panel HUD overlay (reticles, crosshair, REC, params) — Task 4.
   - Pill toggles — Task 4.
   - Full-size segmented buffer LED — Task 4.
   - Captures panel restyling — Task 5.
   - TX LOG comms format with color-coded tags + cyan flash — Task 5.
   - Batch viewer HUD page — Task 6.
   - Reduced motion — Task 7.
   - Smoke test — Task 7.

2. **Placeholder scan** — none. Every step has exact code or exact commands.

3. **Type consistency** — IDs and class names used in JS match the HTML they refer to: `mtimer`, `bootcap`, `vitals`, `v-link/-http/-buf/-bat` (+ `-sub`, `-dot`), `v-buf-segs`, `recdot`, `hudparams`, `bufleds`, `bufcount`, `pill-tog`, `pill-slot`. The `RES_LABELS`, `diopterLabel`, `resselect`, `afselect`, `lensSlider`, `hflipCb`, `vflipCb`, `rotselect`, `_q` references in Task 4's `updateHudParams()` all exist in the pre-existing JS.

4. **Scope check** — one cohesive UI restyle, one file, 7 tasks, one commit per task.
