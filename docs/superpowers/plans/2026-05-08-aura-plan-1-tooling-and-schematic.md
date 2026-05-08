# AURA — Plan 1: Tooling Setup + atopile Schematic Implementation Plan

> **⚠ SUPERSEDED 2026-05-08.** During execution we discovered atopile 0.15.7 has materially different IC declaration patterns than the docs we worked from, and the package registry doesn't yet have the AD8317 / TPS62840 / XIAO module entries we'd need. Pivoted to **SKiDL → KiCad 10 netlist** with a custom `lib/aura.kicad_sym` for the missing ICs. See the Toolchain Amendment in [the spec](../specs/2026-05-08-aura-emf-keychain-pcb-design.md#toolchain-amendment-2026-05-08-post-spec-approval). Execution continues inline (no separate replacement plan document) since the work scope shrank meaningfully — task tracking moved to live todos in the executing-plans session.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the atopile project scaffold and write the complete schematic-as-code for the AURA keychain device, producing an ERC-clean KiCad netlist plus a BOM with LCSC C-numbers ready for layout work in Plan 2.

**Architecture:** atopile (`.ato` files, Python-like schematic-as-code) generates a KiCad netlist that Plan 2 imports into KiCad 8 for layout. The schematic is decomposed into 14 leaf modules grouped under `power/`, `mcu/`, `rf/`, `sensors/`, `haptic/`, `display/`, `io/`. A single top-level `aura.ato` instantiates each leaf and wires them together via typed interfaces (`I2C`, `SPI`, `I2S`, `ElectricPower`). Pin-strapping resistors, decoupling networks, and the `Q1` !VBUS-gating inverter are captured in their respective module files so each module is self-validating.

**Tech Stack:** atopile ≥ 0.9, KiCad 8 (used for footprint validation only in this plan), `uv` for Python tool installation, `git` for version control.

**Spec reference:** [`docs/superpowers/specs/2026-05-08-aura-emf-keychain-pcb-design.md`](../specs/2026-05-08-aura-emf-keychain-pcb-design.md). The spec is the source of truth for every locked decision (pin map, DRC profile, power tree, component variants). This plan covers spec phases **P0** and **P1**.

**Out of scope for this plan** (covered by Plans 2 and 3):
- KiCad project bootstrap, footprint placement, routing, pours, antenna geometry — Plan 2 (placement) and Plan 3 (routing/pours/fab outputs)
- 3D STEP export, Gerbers, drill files — Plan 3
- Hand-tuned RF copper geometry — Plan 3
- Final design notes document with antenna tuning notes and risk verification — Plan 3

---

## File Structure

```
aurapcb/
├── ato.yaml                                 # atopile project config (created Task 0.5)
├── elec/
│   └── src/
│       ├── aura.ato                         # top-level wiring (Task 1.16)
│       ├── power/
│       │   ├── battery.ato                  # BAT± + polyswitch + jumper (Task 1.2)
│       │   ├── buck.ato                     # TPS62840 + L1 + caps + Q1 gate (Task 1.3)
│       │   └── rails.ato                    # 3V3_D ↔ FB1 ↔ 3V3_RF (Task 1.4)
│       ├── mcu/
│       │   ├── xiao_c3.ato                  # XIAO module abstraction (Task 1.5)
│       │   └── pinmap.ato                   # locked D0-D10 net assignments (Task 1.6)
│       ├── rf/
│       │   ├── ad8317.ato                   # log detector + decoupling + LPF (Task 1.7)
│       │   ├── matching.ato                 # CIN + 52.3Ω + DNP tune stub (Task 1.8)
│       │   └── antenna.ato                  # named feed point (Task 1.9)
│       ├── sensors/
│       │   ├── magnetometer.ato             # LIS2MDL @ 0x1E (Task 1.10)
│       │   └── microphone.ato               # ICS-43434 I²S (Task 1.11)
│       ├── haptic/
│       │   └── drv2605l.ato                 # @ 0x5A + LRA pads + ENABLE (Task 1.12)
│       ├── display/
│       │   ├── epaper_fpc.ato               # 24-pin FH12-24S + boost caps (Task 1.13)
│       │   └── epaper_signals.ato           # SPI + DC + RST + CS + BUSY bundle (Task 1.13)
│       └── io/
│           ├── buttons.ato                  # 2× PTS815 + debounce + ESD (Task 1.14)
│           ├── battery_monitor.ato          # 200k/200k + filter (Task 1.15)
│           └── expander.ato                 # TCA9534A @ 0x38 (Task 1.16)
├── build/                                   # ato-generated; gitignored
│   └── aura/
│       ├── aura.kicad_netlist               # KiCad netlist (Task 1.18)
│       └── aura_bom.csv                     # BOM with LCSC C-numbers (Task 1.19)
└── .gitignore                               # add build/ (Task 0.6)
```

Each leaf module is a single self-contained file with one clear responsibility. Top-level `aura.ato` is wires-only — no component decisions live there.

---

## Conventions used in this plan

- All commands assume the working directory is the repo root (`/Users/enes/aurapcb/.claude/worktrees/lucid-driscoll-b77f81/` for the worktree, or the equivalent main checkout).
- "Verify" / "Expected" lines describe what a successful run looks like; if you don't see that, **stop and diagnose** rather than continuing.
- Commit messages follow the spec's convention: imperative mood, no ticket prefix, body explains why if non-obvious.
- For the LCSC part numbers below: every part has been chosen from JLCPCB's Basic or Extended part library. Where multiple LCSC C-numbers exist for an equivalent part, prefer the **Basic** (no extended-part fee). If the chosen part shows as out-of-stock at run time, swap to the listed alternate.
- **TDD-style verification for hardware:** a "test" here means running `ato build` and checking ERC output. ERC must be clean before committing each module.

---

## Phase 0 — Tooling setup

### Task 0.1: Verify `uv` is installed (Python toolchain)

**Files:** none modified.

- [ ] **Step 1: Check uv version**

Run:
```bash
uv --version
```
Expected: prints something like `uv 0.5.x` or higher.

If not installed, install via Homebrew:
```bash
brew install uv
```
Then re-run the version check.

- [ ] **Step 2: No commit** — uv is a system-level tool, not part of the project.

---

### Task 0.2: Install atopile

**Files:** none modified.

- [ ] **Step 1: Install atopile as a uv tool**

Run:
```bash
uv tool install atopile
```
Expected output ends with: `Installed 1 executable: ato`.

- [ ] **Step 2: Verify ato is callable and meets version requirement**

Run:
```bash
ato --version
```
Expected: `ato, version 0.9.x` or higher (we depend on `^0.9.0` in `ato.yaml`).

- [ ] **Step 3: No commit** — system-level install, not project content.

---

### Task 0.3: Verify KiCad 8 CLI is available

**Files:** none modified.

KiCad 8 isn't strictly needed for atopile to produce a netlist, but Plan 2 will need it and we want to fail early if it's missing.

- [ ] **Step 1: Check kicad-cli version**

Run:
```bash
kicad-cli version
```
Expected: prints `8.0.x` or higher.

If KiCad isn't installed, install via Homebrew:
```bash
brew install --cask kicad
```
After installation, the CLI lives at `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`. Symlink it into your PATH if `kicad-cli` isn't found:
```bash
sudo ln -sf /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli /usr/local/bin/kicad-cli
```

- [ ] **Step 2: No commit.**

---

### Task 0.4: Initialize the atopile project scaffold

**Files:**
- Create: `ato.yaml`
- Create: `elec/src/.gitkeep` (placeholder so the directory commits)

**Note:** `ato create` creates a new project in a child directory. Since our repo already exists and has unrelated content (the OrbitaAI files), we'll create the atopile structure in-place by writing `ato.yaml` and the source tree manually.

- [ ] **Step 1: Create the source directory tree**

Run:
```bash
mkdir -p elec/src/power elec/src/mcu elec/src/rf elec/src/sensors elec/src/haptic elec/src/display elec/src/io
touch elec/src/.gitkeep
```

- [ ] **Step 2: Verify the tree**

Run:
```bash
ls -la elec/src/
```
Expected: directories `power/`, `mcu/`, `rf/`, `sensors/`, `haptic/`, `display/`, `io/` and the `.gitkeep` file.

- [ ] **Step 3: No commit yet** — we commit at the end of Task 0.6 when the scaffold is complete.

---

### Task 0.5: Write `ato.yaml`

**Files:**
- Create: `ato.yaml`

- [ ] **Step 1: Write the project config**

Create `ato.yaml` with this exact content:

```yaml
requires-atopile: '^0.9.0'

paths:
    src: 'elec/src'
    layout: 'elec/layouts'

builds:
    aura:
        entry: aura.ato:Aura

dependencies:
    - "atopile/core"
    - "atopile/generics"

# JLCPCB JLC04161H-7628 4-layer ENIG, 0.8 mm
# DRC profile is enforced in KiCad in Plan 2. atopile only validates ERC.
metadata:
    project_name: "AURA"
    description: "EMF-sensing keychain — RF + magnetometer + I2S mic + e-paper + haptic"
    version: "0.1.0"
    fab:
        vendor: "JLCPCB"
        stackup: "JLC04161H-7628"
        layers: 4
        thickness_mm: 0.8
        finish: "ENIG"
```

- [ ] **Step 2: No commit yet.**

---

### Task 0.6: Update `.gitignore` and stage scaffold

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Append atopile-generated paths to `.gitignore`**

Read current `.gitignore`:
```bash
cat .gitignore
```
Expected current content:
```
node_modules
.env
.superpowers/
```

Append the build directory:
```bash
printf '\n# atopile build output\nbuild/\n.ato/\n' >> .gitignore
```

Verify:
```bash
cat .gitignore
```
Expected: now ends with `# atopile build output`, `build/`, `.ato/`.

- [ ] **Step 2: Verify the scaffold is consistent**

Run:
```bash
find elec ato.yaml -type f -o -type d | sort
```
Expected:
```
ato.yaml
elec
elec/src
elec/src/.gitkeep
elec/src/display
elec/src/haptic
elec/src/io
elec/src/mcu
elec/src/power
elec/src/rf
elec/src/sensors
```

- [ ] **Step 3: Commit the scaffold**

Run:
```bash
git add ato.yaml elec/ .gitignore
git commit -m "$(cat <<'EOF'
Scaffold atopile project for AURA EMF keychain

Sets up the elec/src directory tree with placeholders for the 14 leaf
modules per the spec hierarchy, configures ato.yaml with the JLCPCB
fab target, and adds atopile build artifacts to .gitignore.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Verify:
```bash
git log -1 --stat
```
Expected: commit summary shows ~10 files changed (the directory tree + ato.yaml + .gitignore).

---

### Task 0.7: Install atopile dependencies

**Files:**
- Modify: `ato.yaml` (if dependency resolution updates it)
- Created automatically: `.ato/` lockfile dir (gitignored)

- [ ] **Step 1: Run ato install to fetch declared dependencies**

Run:
```bash
ato install
```
Expected: pulls `atopile/core` and `atopile/generics` packages, prints `Installed 2 packages` or similar success.

If a package fails to resolve, check the atopile registry at <https://packages.atopile.io/> for the current package name. The two we want are the standard library (resistors, capacitors, ElectricPower, I2C, SPI, etc.) and a generics collection (commonly-used generic ICs).

- [ ] **Step 2: Verify packages are installed**

Run:
```bash
ls -la .ato/modules/
```
Expected: directories for each installed package.

- [ ] **Step 3: Commit any ato.yaml updates**

If `ato install` modified `ato.yaml` (e.g., pinning resolved versions):
```bash
git add ato.yaml
git diff --cached
```
If there are changes, commit:
```bash
git commit -m "Pin atopile package versions after install"
```
If no changes, skip the commit.

---

### Task 0.8: Document XIAO ESP32-C3 footprint plan

**Files:**
- Create: `docs/footprints/README.md`

The XIAO ESP32-C3 module isn't a standard atopile component. Plan 2 will vendor Seeed's KiCad footprint into the project. For Plan 1, we declare the module in atopile with the correct pad layout but defer the footprint binding.

- [ ] **Step 1: Create a footprints README**

Create `docs/footprints/README.md` with:

```markdown
# Footprint sourcing

Footprints used in this project, with their source and verification status.

| Component | Source | Status | Notes |
|---|---|---|---|
| XIAO ESP32-C3 | Seeed Studio KiCad library (vendored in Plan 2) | TBD | 14 castellated half-holes on long edges |
| AD8317ACPZ-R7 | KiCad standard (`Package_DFN_QFN:LFCSP-8-1EP_3x2mm_P0.5mm_EP1.6x1.4mm`) | Verify against ADI datasheet section 11 in Plan 2 | Exposed pad |
| TPS62840DGRR | KiCad standard (`Package_SO:HVSSOP-8-1EP_3x3mm_P0.65mm_EP1.84x1.74mm`) | Verify in Plan 2 | Thermal pad |
| LIS2MDLTR | KiCad standard (`Package_LGA:LGA-12_2x2mm_P0.5mm_LayoutBorder3x4y`) | Verify in Plan 2 | |
| ICS-43434 | KiCad standard (`Sensor_Audio:Knowles_LGA-6_3.5x2.65mm`) or vendor from InvenSense | Verify in Plan 2 | Acoustic port required |
| DRV2605LDGSR | KiCad standard (`Package_SO:VSSOP-10_3x3mm_P0.5mm`) | Verify in Plan 2 | |
| TCA9534APWR | KiCad standard (`Package_SO:TSSOP-16_4.4x5mm_P0.65mm`) | Verify in Plan 2 | |
| FH12-24S-0.5SH | KiCad standard (`Connector_FFC-FPC:Hirose_FH12-24S-0.5SH_1x24-1MP_P0.50mm_Horizontal`) | Verify pin 1 against e-paper datasheet in Plan 2 | |

Verification means: open the footprint in KiCad's footprint editor, compare pad dimensions and exposed-pad geometry against the manufacturer datasheet, and confirm pin 1 orientation. Mismatches are caught here, not at fab.
```

- [ ] **Step 2: Commit**

```bash
git add docs/footprints/README.md
git commit -m "Document footprint sources and verification plan"
```

---

## Phase 1 — Schematic capture (atopile)

> **For each module task below**: write the `.ato` file, run `ato build aura`, confirm ERC stays clean (or has only the expected "unconnected" warnings for nets the top-level wiring will close), commit. Don't accumulate uncommitted work — small commits make rollback cheap.

### Task 1.1: Write `power/battery.ato`

**Files:**
- Create: `elec/src/power/battery.ato`

This module models the LiPo cell connection: BAT+ pad, BAT– pad, polyswitch + 0Ω bypass jumper in parallel, and a derived `BAT_PLUS_FUSED` rail.

- [ ] **Step 1: Write the module**

Create `elec/src/power/battery.ato`:

```ato
import ElectricPower from "generics/interfaces.ato"
import Resistor from "generics/resistors.ato"

# Polyswitch (resettable fuse) modeled as a Resistor placeholder for ERC purposes;
# the actual part is bound at footprint time in Plan 2 via lcsc and package.
component Polyswitch:
    pin p1
    pin p2
    package = "0805"
    lcsc = "C914043"  # MF-FSMF110X equivalent on JLCPCB
    designator_prefix = "F"

module Battery:
    # External power interfaces
    raw = new ElectricPower      # raw cell, 3.0–4.2 V
    fused = new ElectricPower    # post-polyswitch, feeds buck VIN

    # Asserts on the raw cell
    assert raw.voltage within 3.0V to 4.2V

    # Polyswitch + 0Ω bypass jumper in parallel between raw.hv and fused.hv
    f1 = new Polyswitch
    bypass = new Resistor
    bypass.resistance = 0ohm +/- 1%
    bypass.package = "0805"
    bypass.lcsc = "C17168"  # 0Ω 0805 — populate alone for sleep-Iq measurement

    # Both connect raw.hv to fused.hv; in production, only F1 is populated.
    raw.hv ~ f1.p1
    f1.p2 ~ fused.hv
    raw.hv ~ bypass.p1
    bypass.p2 ~ fused.hv

    # Ground is shared
    raw.lv ~ fused.lv
```

- [ ] **Step 2: Verify with ato build**

Run:
```bash
ato build aura 2>&1 | tail -40
```
Expected: build runs (top-level not yet wired so it'll error on missing `Aura` module — that's expected for now). The important thing is the **module file itself parses** with no syntax errors. Look specifically for parse errors mentioning `power/battery.ato`. If the parser complains about that file, fix the syntax before continuing.

If `ato build` complains about missing imports (`generics/interfaces.ato`, `generics/resistors.ato`), check what `ato install` placed in `.ato/modules/` — the import paths may differ slightly. Adjust to match the installed package layout.

- [ ] **Step 3: Commit**

```bash
git add elec/src/power/battery.ato
git commit -m "Add Battery module with polyswitch + 0Ω bypass jumper"
```

---

### Task 1.2: Write `power/buck.ato`

**Files:**
- Create: `elec/src/power/buck.ato`

The TPS62840 buck regulator with the !VBUS-gated EN, the 2N7002 inverter (Q1), and the inductor + decoupling network. This is the most critical power module — verify carefully.

- [ ] **Step 1: Write the module**

Create `elec/src/power/buck.ato`:

```ato
import ElectricPower, ElectricSignal from "generics/interfaces.ato"
import Resistor from "generics/resistors.ato"
import Capacitor from "generics/capacitors.ato"
import Inductor from "generics/inductors.ato"

# TPS62840 buck regulator. 3.0-5.5 V in, 3.3 V out, 750 mA, 60 nA Iq.
component TPS62840:
    pin VIN
    pin EN
    pin MODE
    pin GND
    pin SW
    pin VOS
    pin FB
    pin VOUT
    package = "HVSSOP-8"
    lcsc = "C2935262"
    designator_prefix = "U"

# 2N7002 N-channel MOSFET, SOT-23. Used as inverter for !VBUS gating.
component MOSFET_NCh_SOT23:
    pin G  # gate
    pin D  # drain
    pin S  # source
    package = "SOT-23"
    lcsc = "C8545"  # 2N7002
    designator_prefix = "Q"

module Buck:
    # External interfaces
    vin = new ElectricPower         # battery-side input (post-polyswitch), 3.0-4.2 V
    vout = new ElectricPower        # 3.3 V output
    vbus_sense = new ElectricSignal # high-impedance sense from XIAO VBUS pad

    assert vout.voltage within 3.27V to 3.33V  # TPS62840 nominal 3.3 V ±1%

    # Buck IC
    u1 = new TPS62840

    # Inductor L1: Murata DFE201610E-2R2M, 2.2 µH, 1 A sat
    l1 = new Inductor
    l1.inductance = 2.2uH +/- 20%
    l1.package = "2520"  # 2.5 × 2.0 mm
    l1.lcsc = "C232037"

    # Input cap: 10 µF X5R 0603 close to VIN
    cin = new Capacitor
    cin.capacitance = 10uF +/- 20%
    cin.package = "0603"
    cin.lcsc = "C19702"

    # Output cap: 10 µF + 100 nF in parallel
    cout_bulk = new Capacitor
    cout_bulk.capacitance = 10uF +/- 20%
    cout_bulk.package = "0603"
    cout_bulk.lcsc = "C19702"

    cout_hf = new Capacitor
    cout_hf.capacitance = 100nF +/- 10%
    cout_hf.package = "0402"
    cout_hf.lcsc = "C1525"

    # !VBUS gate: Q1 inverter
    q1 = new MOSFET_NCh_SOT23

    # 100 kΩ pull-up from EN to BAT+_FUSED (vin.hv) — keeps EN high when Q1 is off
    r_en_pull = new Resistor
    r_en_pull.resistance = 100kohm +/- 1%
    r_en_pull.package = "0402"
    r_en_pull.lcsc = "C25741"

    # 1 MΩ gate-source bleed for Q1 — ensures Q1 turns off cleanly when VBUS is removed
    r_q1_bleed = new Resistor
    r_q1_bleed.resistance = 1Mohm +/- 1%
    r_q1_bleed.package = "0402"
    r_q1_bleed.lcsc = "C25898"

    # ----- Wire up the buck IC -----
    # Power rails
    vin.hv ~ u1.VIN
    vin.lv ~ u1.GND
    u1.GND ~ vout.lv

    # Input cap across VIN/GND
    vin.hv ~ cin.p1
    cin.p2 ~ vin.lv

    # Inductor between SW and VOUT/VOS feedback node
    u1.SW ~ l1.p1
    l1.p2 ~ u1.VOS
    u1.VOS ~ u1.VOUT  # internal feedback short for fixed-output variant
    u1.VOUT ~ vout.hv

    # Output caps across VOUT/GND
    vout.hv ~ cout_bulk.p1
    cout_bulk.p2 ~ vout.lv
    vout.hv ~ cout_hf.p1
    cout_hf.p2 ~ vout.lv

    # MODE → GND for power-save (PFM) mode
    u1.MODE ~ vin.lv

    # ----- !VBUS gating logic -----
    # When VBUS is high (USB plugged in): Q1 on → drain pulls EN to GND → buck off
    # When VBUS is low (USB unplugged): Q1 off → R_EN_PULL pulls EN to BAT+_FUSED → buck on
    vbus_sense.line ~ q1.G
    q1.G ~ r_q1_bleed.p1
    r_q1_bleed.p2 ~ q1.S
    q1.S ~ vin.lv
    q1.D ~ u1.EN
    u1.EN ~ r_en_pull.p1
    r_en_pull.p2 ~ vin.hv
```

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep -A2 "buck.ato\|error"
```
Expected: no syntax errors specifically attributed to `buck.ato`. (Top-level errors about `Aura` module not yet existing are expected.)

- [ ] **Step 3: Commit**

```bash
git add elec/src/power/buck.ato
git commit -m "Add Buck module — TPS62840 + L1 + Q1 !VBUS gating"
```

---

### Task 1.3: Write `power/rails.ato`

**Files:**
- Create: `elec/src/power/rails.ato`

The single-point ferrite-bead split between 3V3_D and 3V3_RF.

- [ ] **Step 1: Write the module**

Create `elec/src/power/rails.ato`:

```ato
import ElectricPower from "generics/interfaces.ato"

# Ferrite bead BLM18PG471SN1D — 470 Ω @ 100 MHz, 1 A
component FerriteBead:
    pin p1
    pin p2
    package = "0603"
    lcsc = "C159334"  # BLM18PG471SN1D equivalent
    designator_prefix = "FB"

module Rails:
    # Inputs / outputs
    in_3v3 = new ElectricPower      # from buck output, this is "3V3_D"
    out_3v3_rf = new ElectricPower  # ferrite-isolated, AD8317 only

    # Single ferrite bead, single-point connection
    fb1 = new FerriteBead
    in_3v3.hv ~ fb1.p1
    fb1.p2 ~ out_3v3_rf.hv

    # GND is shared
    in_3v3.lv ~ out_3v3_rf.lv
```

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "rails.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/power/rails.ato
git commit -m "Add Rails module — FB1 split between 3V3_D and 3V3_RF"
```

---

### Task 1.4: Write `mcu/xiao_c3.ato`

**Files:**
- Create: `elec/src/mcu/xiao_c3.ato`

Models the XIAO ESP32-C3 module as a 14-pad component with named pins matching the silkscreen labels (D0–D10, 3V3, 5V/VBUS, GND). The actual KiCad footprint binding happens in Plan 2.

- [ ] **Step 1: Write the module**

Create `elec/src/mcu/xiao_c3.ato`:

```ato
import ElectricPower, ElectricSignal, I2C, SPI, I2S from "generics/interfaces.ato"

# Seeed XIAO ESP32-C3 module — 14-pad daughterboard
# 21 × 17.5 mm castellated half-holes on long edges + 4 GND vias
# Footprint vendored from Seeed KiCad library in Plan 2
component XIAO_ESP32_C3:
    pin D0      # GPIO2  / ADC1_2 — strap pin
    pin D1      # GPIO3  / ADC1_3
    pin D2      # GPIO4  / ADC1_4
    pin D3      # GPIO5  / ADC1_5
    pin D4      # GPIO6  / I2C SDA default
    pin D5      # GPIO7  / I2C SCL default
    pin D6      # GPIO21 / U0TXD default
    pin D7      # GPIO20 / U0RXD default
    pin D8      # GPIO8  — strap pin
    pin D9      # GPIO9  — strap pin (SPI MISO default)
    pin D10     # GPIO10 / SPI MOSI default
    pin V3V3    # 3.3 V power (when buck on, otherwise XIAO LDO drives this)
    pin V5V     # USB-C VBUS sense pad
    pin GND     # ≥ 4 GND vias to L2

    package = "Module_XIAO_ESP32_C3"  # vendored footprint in Plan 2
    lcsc = "C2934897"  # Seeed SKU 102991060 — XIAO ESP32-C3
    designator_prefix = "U"

module XiaoC3:
    """Wraps the XIAO ESP32-C3 module and exposes typed interfaces.

    The pin-to-interface mapping is specified in mcu/pinmap.ato so that
    swapping the breakout (e.g., to XIAO ESP32-S3) requires only changing
    the pinmap, not the rest of the schematic.
    """
    u1 = new XIAO_ESP32_C3

    # Power
    power3v3 = new ElectricPower
    vbus_sense = new ElectricSignal

    # Communication buses
    i2c = new I2C
    spi = new SPI
    i2s = new I2S

    # Direct GPIO signals (one ElectricSignal each — no I/O direction enforcement at ERC)
    epd_dc = new ElectricSignal           # D0 / GPIO2
    bat_sense = new ElectricSignal        # D1 / GPIO3 — ADC input
    ad8317_vout_filt = new ElectricSignal # D2 / GPIO4 — ADC input
    exp_int = new ElectricSignal          # D3 / GPIO5 — interrupt input

    # Connect power
    power3v3.hv ~ u1.V3V3
    power3v3.lv ~ u1.GND

    # VBUS sense (high-Z; no current draw on main board)
    vbus_sense.line ~ u1.V5V
    vbus_sense.reference ~ power3v3.lv

    # I2C bus (D4/D5)
    i2c.sda.line ~ u1.D4
    i2c.scl.line ~ u1.D5
    i2c.sda.reference ~ power3v3.lv
    i2c.scl.reference ~ power3v3.lv

    # SPI (D9/D10)
    spi.sclk.line ~ u1.D9
    spi.mosi.line ~ u1.D10
    spi.sclk.reference ~ power3v3.lv
    spi.mosi.reference ~ power3v3.lv

    # I2S (D6/D7/D8): BCLK, LRCLK, DIN
    i2s.bclk.line ~ u1.D6
    i2s.lrclk.line ~ u1.D7
    i2s.din.line ~ u1.D8
    i2s.bclk.reference ~ power3v3.lv
    i2s.lrclk.reference ~ power3v3.lv
    i2s.din.reference ~ power3v3.lv

    # Direct GPIO signals
    epd_dc.line ~ u1.D0
    bat_sense.line ~ u1.D1
    ad8317_vout_filt.line ~ u1.D2
    exp_int.line ~ u1.D3
    epd_dc.reference ~ power3v3.lv
    bat_sense.reference ~ power3v3.lv
    ad8317_vout_filt.reference ~ power3v3.lv
    exp_int.reference ~ power3v3.lv
```

**Note on the I2S interface:** atopile's stdlib may or may not include an `I2S` interface. If `import I2S from "generics/interfaces.ato"` fails, fall back to defining I2S inline at the top of `xiao_c3.ato`:

```ato
interface I2S:
    bclk = new ElectricSignal
    lrclk = new ElectricSignal
    din = new ElectricSignal
```

Then the rest of the module uses `i2s.bclk.line`, etc. as written. **Do NOT define I2S in a shared file** — keep it local to `xiao_c3.ato` for now; if a second module needs it (the microphone, in Task 1.11), promote the interface to its own file at that time.

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "xiao_c3.ato\|error" | head -20
```
Expected: no syntax errors. If `I2S` doesn't resolve, apply the inline-definition fallback above.

- [ ] **Step 3: Commit**

```bash
git add elec/src/mcu/xiao_c3.ato
git commit -m "Add XiaoC3 module wrapping the XIAO ESP32-C3 with typed interfaces"
```

---

### Task 1.5: Write `mcu/pinmap.ato` — strap pull-ups for D8 and D9

**Files:**
- Create: `elec/src/mcu/pinmap.ato`

GPIO8 (D8 / I2S_DIN) and GPIO9 (D9 / SPI_SCK) are ESP32-C3 boot strapping pins that must be high during reset. We enforce this in the schematic with explicit 10 kΩ pull-ups so the schematic is self-documenting.

- [ ] **Step 1: Write the module**

Create `elec/src/mcu/pinmap.ato`:

```ato
import ElectricPower, ElectricSignal from "generics/interfaces.ato"
import Resistor from "generics/resistors.ato"

module StrappingPullups:
    """10 kΩ pull-ups for ESP32-C3 strap pins D8 (I2S_DIN) and D9 (SPI_SCK).

    GPIO 2/8/9 are boot mode strapping. GPIO2 (D0/EPD_DC) is driven by MCU
    output and idles high — fine. GPIO8 (D8) is an I2S input from the mic;
    high-Z when L/R clock idles, so a pull-up is required. GPIO9 (D9) is
    SPI SCK in mode 0 which idles low — would force download mode at boot
    without a pull-up.
    """
    power3v3 = new ElectricPower
    d8_strap = new ElectricSignal  # I2S_DIN — pulled high
    d9_strap = new ElectricSignal  # SPI_SCK — pulled high

    r_d8 = new Resistor
    r_d8.resistance = 10kohm +/- 5%
    r_d8.package = "0402"
    r_d8.lcsc = "C25744"

    r_d9 = new Resistor
    r_d9.resistance = 10kohm +/- 5%
    r_d9.package = "0402"
    r_d9.lcsc = "C25744"

    d8_strap.line ~ r_d8.p1
    r_d8.p2 ~ power3v3.hv
    d8_strap.reference ~ power3v3.lv

    d9_strap.line ~ r_d9.p1
    r_d9.p2 ~ power3v3.hv
    d9_strap.reference ~ power3v3.lv
```

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "pinmap.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/mcu/pinmap.ato
git commit -m "Add StrappingPullups module for ESP32-C3 D8 and D9 strap pins"
```

---

### Task 1.6: Write `rf/ad8317.ato`

**Files:**
- Create: `elec/src/rf/ad8317.ato`

The AD8317 logarithmic detector with its three-cap decoupling stack (100 nF + 10 nF + 1 nF), the CLPF cap, and the RC LPF on the VOUT path.

- [ ] **Step 1: Write the module**

Create `elec/src/rf/ad8317.ato`:

```ato
import ElectricPower, ElectricSignal from "generics/interfaces.ato"
import Resistor from "generics/resistors.ato"
import Capacitor from "generics/capacitors.ato"

# Analog Devices AD8317ACPZ-R7 — broadband (1 MHz–10 GHz) log detector
# LFCSP-8, 3 × 2 mm, exposed pad (paddle = GND)
component AD8317:
    pin VPOS    # 1 — supply, 3.3 V
    pin ENBL    # 2 — enable; high to power up
    pin RFIN    # 3 — RF input
    pin INHI    # 4 — internal node (tied to RFIN externally? per datasheet)
    pin INLO    # 5 — internal node, AC-coupled to GND
    pin VSET    # 6 — set point input; tie to VOUT for detector mode
    pin VOUT    # 7 — output voltage
    pin CLPF    # 8 — output filter cap
    pin EP      # exposed pad — GND
    package = "LFCSP-8-1EP_3x2mm_P0.5mm_EP1.6x1.4mm"
    lcsc = "C485486"  # AD8317ACPZ-R7
    designator_prefix = "U"

module Ad8317Detector:
    """AD8317 log detector with full decoupling and output LPF.

    Per ADI datasheet figure 38 reference design:
    - VPOS decoupled with 100 nF || 10 nF || 1 nF X7R (within 1 mm)
    - INLO AC-coupled to GND with 1 nF
    - CLPF: 1 nF for default output bandwidth
    - VSET tied to VOUT (detector mode, VSET feedback disabled)
    - VOUT through 1 kΩ + 100 nF RC LPF before going off-module
    """
    power3v3rf = new ElectricPower    # 3V3_RF (ferrite-isolated)
    rfin = new ElectricSignal         # 50 Ω RF input from antenna
    enbl = new ElectricSignal         # enable from expander
    vout_filtered = new ElectricSignal # filtered analog out to XIAO ADC

    u2 = new AD8317

    # Decoupling stack on VPOS — within 1 mm of pin
    c_vpos_100n = new Capacitor
    c_vpos_100n.capacitance = 100nF +/- 10%
    c_vpos_100n.package = "0402"
    c_vpos_100n.lcsc = "C1525"

    c_vpos_10n = new Capacitor
    c_vpos_10n.capacitance = 10nF +/- 10%
    c_vpos_10n.package = "0402"
    c_vpos_10n.lcsc = "C1546"

    c_vpos_1n = new Capacitor
    c_vpos_1n.capacitance = 1nF +/- 10%
    c_vpos_1n.package = "0402"
    c_vpos_1n.lcsc = "C1588"

    # CLPF — output filter cap (default 1 nF for max bandwidth)
    c_clpf = new Capacitor
    c_clpf.capacitance = 1nF +/- 10%
    c_clpf.package = "0402"
    c_clpf.lcsc = "C1588"

    # INLO AC-couple to GND — 1 nF
    c_inlo = new Capacitor
    c_inlo.capacitance = 1nF +/- 10%
    c_inlo.package = "0402"
    c_inlo.lcsc = "C1588"

    # Output RC LPF — 1 kΩ series + 100 nF shunt
    r_lpf = new Resistor
    r_lpf.resistance = 1kohm +/- 1%
    r_lpf.package = "0402"
    r_lpf.lcsc = "C11702"

    c_lpf = new Capacitor
    c_lpf.capacitance = 100nF +/- 10%
    c_lpf.package = "0402"
    c_lpf.lcsc = "C1525"

    # ----- Wiring -----
    # Power
    power3v3rf.hv ~ u2.VPOS
    power3v3rf.lv ~ u2.EP   # exposed pad to GND

    # VPOS decoupling — all caps to GND
    u2.VPOS ~ c_vpos_100n.p1
    c_vpos_100n.p2 ~ power3v3rf.lv
    u2.VPOS ~ c_vpos_10n.p1
    c_vpos_10n.p2 ~ power3v3rf.lv
    u2.VPOS ~ c_vpos_1n.p1
    c_vpos_1n.p2 ~ power3v3rf.lv

    # Enable input
    enbl.line ~ u2.ENBL
    enbl.reference ~ power3v3rf.lv

    # RF input
    rfin.line ~ u2.RFIN
    rfin.reference ~ power3v3rf.lv

    # Per datasheet: tie INHI to RFIN (internal differential input pair)
    u2.INHI ~ u2.RFIN

    # INLO AC-couple to GND
    u2.INLO ~ c_inlo.p1
    c_inlo.p2 ~ power3v3rf.lv

    # CLPF output filter
    u2.CLPF ~ c_clpf.p1
    c_clpf.p2 ~ power3v3rf.lv

    # VSET tied to VOUT (detector mode)
    u2.VSET ~ u2.VOUT

    # Output LPF: VOUT through 1 kΩ to vout_filtered.line; 100 nF from filtered to GND
    u2.VOUT ~ r_lpf.p1
    r_lpf.p2 ~ vout_filtered.line
    vout_filtered.line ~ c_lpf.p1
    c_lpf.p2 ~ power3v3rf.lv
    vout_filtered.reference ~ power3v3rf.lv
```

**Note on VSET:** the AD8317 datasheet describes two modes — controller (VSET driven externally) and detector (VSET tied to VOUT). We use detector mode. If the docs you read disagree on the INHI/INLO routing, defer to the AD8317ACPZ-R7 datasheet figure 38 (the recommended single-ended detector reference design).

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "ad8317.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/rf/ad8317.ato
git commit -m "Add Ad8317Detector module — log detector + decoupling + output LPF"
```

---

### Task 1.7: Write `rf/matching.ato`

**Files:**
- Create: `elec/src/rf/matching.ato`

The 1 nF AC-couple cap, the 52.3 Ω shunt termination, and the DNP tuning stub footprint between antenna feed and AD8317 RFIN.

- [ ] **Step 1: Write the module**

Create `elec/src/rf/matching.ato`:

```ato
import ElectricPower, ElectricSignal from "generics/interfaces.ato"
import Resistor from "generics/resistors.ato"
import Capacitor from "generics/capacitors.ato"

module RfMatching:
    """Antenna-to-AD8317 input matching network.

    Per ADI AD8317 datasheet figure 38:
      ANT --[CIN 1nF]-- RFIN_node --[52.3Ω shunt to GND]
                            |
                            +-- [DNP tuning stub footprint]
                            |
                            v
                          AD8317.RFIN

    The DNP stub provides post-fab impedance correction if VNA S11 is poor.
    Default value 0Ω footprint, populate 1.0–4.7 pF based on sweep.
    """
    power_ref = new ElectricPower    # for the GND reference of the shunt
    ant_in = new ElectricSignal      # antenna feed
    rfin_out = new ElectricSignal    # to AD8317 RFIN

    # AC-couple cap CIN: 1 nF X7R 0402
    c_in = new Capacitor
    c_in.capacitance = 1nF +/- 10%
    c_in.package = "0402"
    c_in.lcsc = "C1588"

    # Shunt termination 52.3 Ω 1% 0402 — on antenna side of CIN
    r_term = new Resistor
    r_term.resistance = 52.3ohm +/- 1%
    r_term.package = "0402"
    r_term.lcsc = "C12779"  # 52.3R 0402 1%

    # DNP tuning stub footprint — 0Ω 0402, populate as cap if needed
    r_tune = new Resistor
    r_tune.resistance = 0ohm +/- 1%
    r_tune.package = "0402"
    r_tune.lcsc = "C17168"  # 0Ω 0402; spec'd as DNP at fab

    # ----- Wiring -----
    # CIN: antenna → AC couple → RFIN
    ant_in.line ~ c_in.p1
    c_in.p2 ~ rfin_out.line

    # Shunt termination: from antenna side of CIN to GND
    ant_in.line ~ r_term.p1
    r_term.p2 ~ power_ref.lv

    # Tuning stub: between RFIN and GND, populated only if needed
    rfin_out.line ~ r_tune.p1
    r_tune.p2 ~ power_ref.lv

    # Reference grounds
    ant_in.reference ~ power_ref.lv
    rfin_out.reference ~ power_ref.lv
```

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "matching.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/rf/matching.ato
git commit -m "Add RfMatching module — CIN + 52.3Ω shunt + DNP tune stub"
```

---

### Task 1.8: Write `rf/antenna.ato`

**Files:**
- Create: `elec/src/rf/antenna.ato`

The antenna is a copper trace, not a discrete component, so this module is mostly a documented placeholder for the named feed-point net. The actual meander geometry is drawn in KiCad in Plan 3.

- [ ] **Step 1: Write the module**

Create `elec/src/rf/antenna.ato`:

```ato
import ElectricSignal from "generics/interfaces.ato"

module Antenna:
    """Meander monopole antenna — copper geometry, no discrete component.

    Geometry parameters (drawn in KiCad, Plan 3):
      - Type: end-fed meander monopole, 4 hairpins
      - Trace width: 0.30 mm (50 Ω microstrip on 0.16 mm prepreg)
      - Total length: ~25 mm (~λ/4 at 1.5 GHz center)
      - Bounding box: 12 × 4 mm
      - Coverage: 500 MHz – 3 GHz
      - Keep-out: 5 mm halo, NO COPPER on any of L1–L4
      - Validation: VNA S11 sweep required on first build, target S11 < –6 dB
        across 700 MHz – 2.6 GHz

    This module exposes only the feed signal. The 50 Ω microstrip from
    feed to AD8317 RFIN must be ≤ 5 mm and have no vias.
    """
    feed = new ElectricSignal    # antenna feed point — wire to RfMatching.ant_in

    # No physical components — geometry only.
    # Marker net so KiCad layout has a named target for the feed.
    # (The actual radiating copper is drawn in pcbnew, not generated by atopile.)
```

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "antenna.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/rf/antenna.ato
git commit -m "Add Antenna module — meander feed-point net (geometry in KiCad)"
```

---

### Task 1.9: Write `sensors/magnetometer.ato`

**Files:**
- Create: `elec/src/sensors/magnetometer.ato`

LIS2MDL three-axis magnetometer, I²C address `0x1E`, with VDD/VDDIO decoupling and the C0G "Cap" pin reservoir per datasheet.

- [ ] **Step 1: Write the module**

Create `elec/src/sensors/magnetometer.ato`:

```ato
import ElectricPower, ElectricSignal, I2C from "generics/interfaces.ato"
import Capacitor from "generics/capacitors.ato"

# ST LIS2MDLTR — 3-axis magnetometer, LGA-12 2 × 2 mm
# I²C address 0x1E (when SA pin tied to GND)
component LIS2MDL:
    pin SCL
    pin SDA
    pin DRDY
    pin INT
    pin VDD
    pin VDDIO
    pin GND
    pin C1     # reservoir cap pin
    pin RES    # reserved — connect to GND per datasheet
    pin SA     # I²C address select; tie low for 0x1E
    pin CS     # I²C/SPI mode select; tie high for I²C
    package = "LGA-12_2x2mm_P0.5mm_LayoutBorder3x4y"
    lcsc = "C504428"
    designator_prefix = "U"

module Magnetometer:
    """LIS2MDL @ 0x1E with decoupling and DRDY output."""
    power3v3 = new ElectricPower
    i2c = new I2C
    drdy = new ElectricSignal

    u4 = new LIS2MDL

    # Decoupling: 100 nF at VDD + 100 nF at VDDIO
    c_vdd = new Capacitor
    c_vdd.capacitance = 100nF +/- 10%
    c_vdd.package = "0402"
    c_vdd.lcsc = "C1525"

    c_vddio = new Capacitor
    c_vddio.capacitance = 100nF +/- 10%
    c_vddio.package = "0402"
    c_vddio.lcsc = "C1525"

    # Reservoir cap on C1 pin — 100 nF C0G per datasheet
    c_res = new Capacitor
    c_res.capacitance = 100nF +/- 10%
    c_res.package = "0402"
    c_res.lcsc = "C307331"  # C0G/NP0 dielectric

    # ----- Wiring -----
    power3v3.hv ~ u4.VDD
    power3v3.hv ~ u4.VDDIO
    power3v3.lv ~ u4.GND
    power3v3.lv ~ u4.RES   # reserved → GND

    # Decoupling
    u4.VDD ~ c_vdd.p1
    c_vdd.p2 ~ power3v3.lv
    u4.VDDIO ~ c_vddio.p1
    c_vddio.p2 ~ power3v3.lv

    # Reservoir
    u4.C1 ~ c_res.p1
    c_res.p2 ~ power3v3.lv

    # I²C — address strap to GND for 0x1E, CS high for I²C mode
    u4.SA ~ power3v3.lv
    u4.CS ~ power3v3.hv
    i2c.sda.line ~ u4.SDA
    i2c.scl.line ~ u4.SCL
    i2c.sda.reference ~ power3v3.lv
    i2c.scl.reference ~ power3v3.lv

    # Data-ready output (to expander P6)
    drdy.line ~ u4.DRDY
    drdy.reference ~ power3v3.lv

    # INT not used in this design — leave as no-connect (atopile permits dangling pins;
    # we'll add a no-connect marker in KiCad in Plan 2)
```

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "magnetometer.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/sensors/magnetometer.ato
git commit -m "Add Magnetometer module — LIS2MDL @ 0x1E"
```

---

### Task 1.10: Write `sensors/microphone.ato`

**Files:**
- Create: `elec/src/sensors/microphone.ato`

ICS-43434 I²S MEMS microphone, L/R tied to GND for left-channel-only operation.

- [ ] **Step 1: Write the module**

Create `elec/src/sensors/microphone.ato`:

```ato
import ElectricPower, I2S from "generics/interfaces.ato"
import Capacitor from "generics/capacitors.ato"

# InvenSense ICS-43434 — I²S MEMS microphone, LGA-6 3.5 × 2.65 mm
# Acoustic port on bottom; PCB requires a 1 mm hole aligned to enclosure opening
component ICS43434:
    pin LR        # left/right select; tie low for left channel
    pin WS        # word select / LRCLK
    pin SCK       # bit clock / BCLK
    pin SD        # serial data out
    pin VDD
    pin GND
    package = "Knowles_LGA-6_3.5x2.65mm"
    lcsc = "C353473"
    designator_prefix = "U"

# I²S interface fallback if not in stdlib — see note in Task 1.4
# If stdlib import fails:
# interface I2S:
#     bclk = new ElectricSignal
#     lrclk = new ElectricSignal
#     din = new ElectricSignal

module Microphone:
    """ICS-43434 I²S MEMS microphone, left channel."""
    power3v3 = new ElectricPower
    i2s = new I2S

    u5 = new ICS43434

    # Decoupling: 100 nF + 10 µF X5R per datasheet
    c_dec_hf = new Capacitor
    c_dec_hf.capacitance = 100nF +/- 10%
    c_dec_hf.package = "0402"
    c_dec_hf.lcsc = "C1525"

    c_dec_bulk = new Capacitor
    c_dec_bulk.capacitance = 10uF +/- 20%
    c_dec_bulk.package = "0603"
    c_dec_bulk.lcsc = "C19702"

    # ----- Wiring -----
    power3v3.hv ~ u5.VDD
    power3v3.lv ~ u5.GND

    u5.VDD ~ c_dec_hf.p1
    c_dec_hf.p2 ~ power3v3.lv
    u5.VDD ~ c_dec_bulk.p1
    c_dec_bulk.p2 ~ power3v3.lv

    # L/R = GND for left channel
    u5.LR ~ power3v3.lv

    # I²S — clocks driven by MCU, data out from mic
    i2s.bclk.line ~ u5.SCK
    i2s.lrclk.line ~ u5.WS
    i2s.din.line ~ u5.SD
    i2s.bclk.reference ~ power3v3.lv
    i2s.lrclk.reference ~ power3v3.lv
    i2s.din.reference ~ power3v3.lv
```

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "microphone.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/sensors/microphone.ato
git commit -m "Add Microphone module — ICS-43434 I²S MEMS, left channel"
```

---

### Task 1.11: Write `haptic/drv2605l.ato`

**Files:**
- Create: `elec/src/haptic/drv2605l.ato`

DRV2605L haptic driver, I²C address `0x5A`, with ENABLE controlled by the expander (saves 1.7 mA standby).

- [ ] **Step 1: Write the module**

Create `elec/src/haptic/drv2605l.ato`:

```ato
import ElectricPower, ElectricSignal, I2C from "generics/interfaces.ato"
import Capacitor from "generics/capacitors.ato"

# TI DRV2605LDGSR — haptic motor driver, VSSOP-10 3 × 3 mm
# I²C address 0x5A (default)
component DRV2605L:
    pin REG     # internal regulator output — 1 µF cap to GND
    pin VDD     # supply
    pin GND
    pin SDA
    pin SCL
    pin EN      # enable; high to power up
    pin IN_TRIG # PWM/trigger input — leave NC in I²C-only mode
    pin OUT_P   # LRA drive output positive
    pin OUT_N   # LRA drive output negative
    pin NC      # unused
    package = "VSSOP-10_3x3mm_P0.5mm"
    lcsc = "C92482"
    designator_prefix = "U"

module Drv2605l:
    """DRV2605L @ 0x5A. ENABLE on expander P7 to gate 1.7 mA standby."""
    power3v3 = new ElectricPower
    i2c = new I2C
    enable = new ElectricSignal

    # LRA pads exposed as a power interface (motor doesn't fit ElectricSignal model
    # cleanly; use ElectricPower with hv = OUT_P and lv = OUT_N)
    lra = new ElectricPower

    u3 = new DRV2605L

    # Bulk + HF decoupling on VDD: 1 µF + 100 nF, within 2 mm
    c_vdd_bulk = new Capacitor
    c_vdd_bulk.capacitance = 1uF +/- 10%
    c_vdd_bulk.package = "0402"
    c_vdd_bulk.lcsc = "C52923"

    c_vdd_hf = new Capacitor
    c_vdd_hf.capacitance = 100nF +/- 10%
    c_vdd_hf.package = "0402"
    c_vdd_hf.lcsc = "C1525"

    # Internal regulator cap — 1 µF on REG pin per datasheet
    c_reg = new Capacitor
    c_reg.capacitance = 1uF +/- 10%
    c_reg.package = "0402"
    c_reg.lcsc = "C52923"

    # ----- Wiring -----
    power3v3.hv ~ u3.VDD
    power3v3.lv ~ u3.GND

    # VDD decoupling
    u3.VDD ~ c_vdd_bulk.p1
    c_vdd_bulk.p2 ~ power3v3.lv
    u3.VDD ~ c_vdd_hf.p1
    c_vdd_hf.p2 ~ power3v3.lv

    # REG cap
    u3.REG ~ c_reg.p1
    c_reg.p2 ~ power3v3.lv

    # I²C
    i2c.sda.line ~ u3.SDA
    i2c.scl.line ~ u3.SCL
    i2c.sda.reference ~ power3v3.lv
    i2c.scl.reference ~ power3v3.lv

    # Enable from expander
    enable.line ~ u3.EN
    enable.reference ~ power3v3.lv

    # IN_TRIG and NC — left dangling, no-connect markers added in KiCad

    # LRA differential output (motor body is enclosure-mounted; pads only)
    lra.hv ~ u3.OUT_P
    lra.lv ~ u3.OUT_N
```

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "drv2605l.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/haptic/drv2605l.ato
git commit -m "Add Drv2605l module — haptic driver @ 0x5A with expander-gated ENABLE"
```

---

### Task 1.12: Write `display/epaper_fpc.ato`

**Files:**
- Create: `elec/src/display/epaper_fpc.ato`

The 24-pin FH12-24S-0.5SH FPC connector + the 4× 1 µF e-paper boost circuit caps.

- [ ] **Step 1: Write the module**

Create `elec/src/display/epaper_fpc.ato`:

```ato
import ElectricPower, ElectricSignal, SPI from "generics/interfaces.ato"
import Capacitor from "generics/capacitors.ato"

# Hirose FH12-24S-0.5SH — 24-pin 0.5 mm pitch FPC, top contact
# Pinout per Good Display GDEW0102I4FC class panels
component FH12_24S_FPC:
    pin P1   # VDD
    pin P2   # GND
    pin P3   # BS (bus select; tie to GND for 4-line SPI)
    pin P4   # BUSY
    pin P5   # RES
    pin P6   # D/C
    pin P7   # CS
    pin P8   # SCL (SPI clock)
    pin P9   # SDA (SPI MOSI)
    pin P10  # NC
    pin P11  # NC
    pin P12  # VCOM
    pin P13  # VSL boost cap
    pin P14  # VSH boost cap
    pin P15  # VPP (program voltage; tie to VDD per most modules)
    pin P16  # VGL boost cap
    pin P17  # VGH boost cap
    pin P18  # NC
    pin P19  # NC
    pin P20  # NC
    pin P21  # NC
    pin P22  # GND_PE
    pin P23  # GND
    pin P24  # GND
    package = "Hirose_FH12-24S-0.5SH_1x24-1MP_P0.50mm_Horizontal"
    lcsc = "C90105"
    designator_prefix = "J"

module EpaperFpc:
    """24-pin FPC connector for e-paper module + boost circuit caps.

    Boost caps (4 × 1 µF X5R 0402) must be placed within 5 mm of the FPC
    per spec — they support the on-glass DC/DC for the panel drive voltages.
    """
    power3v3 = new ElectricPower
    spi = new SPI
    dc = new ElectricSignal
    rst = new ElectricSignal
    cs = new ElectricSignal
    busy = new ElectricSignal

    j1 = new FH12_24S_FPC

    # Boost circuit caps (VSL, VSH, VGL, VGH)
    c_vsl = new Capacitor
    c_vsl.capacitance = 1uF +/- 10%
    c_vsl.package = "0402"
    c_vsl.lcsc = "C52923"

    c_vsh = new Capacitor
    c_vsh.capacitance = 1uF +/- 10%
    c_vsh.package = "0402"
    c_vsh.lcsc = "C52923"

    c_vgl = new Capacitor
    c_vgl.capacitance = 1uF +/- 10%
    c_vgl.package = "0402"
    c_vgl.lcsc = "C52923"

    c_vgh = new Capacitor
    c_vgh.capacitance = 1uF +/- 10%
    c_vgh.package = "0402"
    c_vgh.lcsc = "C52923"

    # ----- Wiring -----
    # Power + GND pins
    power3v3.hv ~ j1.P1
    power3v3.hv ~ j1.P15  # VPP tied to VDD
    power3v3.lv ~ j1.P2
    power3v3.lv ~ j1.P22
    power3v3.lv ~ j1.P23
    power3v3.lv ~ j1.P24

    # 4-wire SPI mode: BS pin to GND
    j1.P3 ~ power3v3.lv

    # Control signals
    busy.line ~ j1.P4
    rst.line ~ j1.P5
    dc.line ~ j1.P6
    cs.line ~ j1.P7
    busy.reference ~ power3v3.lv
    rst.reference ~ power3v3.lv
    dc.reference ~ power3v3.lv
    cs.reference ~ power3v3.lv

    # SPI
    spi.sclk.line ~ j1.P8
    spi.mosi.line ~ j1.P9
    spi.sclk.reference ~ power3v3.lv
    spi.mosi.reference ~ power3v3.lv

    # Boost circuit caps to GND
    j1.P13 ~ c_vsl.p1
    c_vsl.p2 ~ power3v3.lv
    j1.P14 ~ c_vsh.p1
    c_vsh.p2 ~ power3v3.lv
    j1.P16 ~ c_vgl.p1
    c_vgl.p2 ~ power3v3.lv
    j1.P17 ~ c_vgh.p1
    c_vgh.p2 ~ power3v3.lv

    # VCOM — leave NC; some panels need it tied to VDD via a cap. Verify
    # against the chosen e-paper module datasheet during BOM finalization.

    # NC pins (P10, P11, P18, P19, P20, P21) — left dangling
```

**Note on e-paper module variations:** the GDEW0102I4FC pinout above is one common 24-pin variant. If the chosen e-paper module uses a different pinout (some are GDEW or DESPI variants), update the pin assignments here before Task 1.16. The atopile structure stays the same — only the wiring inside `EpaperFpc` changes.

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "epaper_fpc.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/display/epaper_fpc.ato
git commit -m "Add EpaperFpc module — 24-pin Hirose FPC + 4 boost caps"
```

---

### Task 1.13: Write `io/buttons.ato`

**Files:**
- Create: `elec/src/io/buttons.ato`

Two C&K PTS815 tactile switches with 100 nF debounce caps and 1 kΩ ESD series resistors.

- [ ] **Step 1: Write the module**

Create `elec/src/io/buttons.ato`:

```ato
import ElectricPower, ElectricSignal from "generics/interfaces.ato"
import Resistor from "generics/resistors.ato"
import Capacitor from "generics/capacitors.ato"

# C&K PTS815 — 4 × 3 mm SMD tactile switch
component PTS815:
    pin p1
    pin p2
    package = "SW_SPST_PTS815"  # vendor or use standard 4x3 SMD tactile footprint
    lcsc = "C720477"
    designator_prefix = "SW"

module Button:
    """One tactile switch with 100 nF debounce + 1 kΩ ESD series.

    Wiring: GPIO --[1kΩ]--+-- switch -- GND
                          |
                          +-- 100nF --- GND
    """
    power_ref = new ElectricPower
    gpio = new ElectricSignal      # to expander port (input, pulled up internally)

    sw = new PTS815

    r_esd = new Resistor
    r_esd.resistance = 1kohm +/- 5%
    r_esd.package = "0402"
    r_esd.lcsc = "C11702"

    c_deb = new Capacitor
    c_deb.capacitance = 100nF +/- 10%
    c_deb.package = "0402"
    c_deb.lcsc = "C1525"

    # Wiring
    gpio.line ~ r_esd.p1
    r_esd.p2 ~ sw.p1
    sw.p2 ~ power_ref.lv
    r_esd.p2 ~ c_deb.p1
    c_deb.p2 ~ power_ref.lv

    gpio.reference ~ power_ref.lv

module Buttons:
    """Two-button assembly: BTN_LEFT and BTN_RIGHT."""
    power_ref = new ElectricPower
    btn_left = new ElectricSignal
    btn_right = new ElectricSignal

    left = new Button
    right = new Button

    btn_left ~ left.gpio
    btn_right ~ right.gpio
    power_ref ~ left.power_ref
    power_ref ~ right.power_ref
```

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "buttons.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/io/buttons.ato
git commit -m "Add Buttons module — 2× PTS815 with debounce + ESD"
```

---

### Task 1.14: Write `io/battery_monitor.ato`

**Files:**
- Create: `elec/src/io/battery_monitor.ato`

200 kΩ + 200 kΩ divider with a 100 nF filter cap on the midpoint, feeding the XIAO ADC.

- [ ] **Step 1: Write the module**

Create `elec/src/io/battery_monitor.ato`:

```ato
import ElectricPower, ElectricSignal from "generics/interfaces.ato"
import Resistor from "generics/resistors.ato"
import Capacitor from "generics/capacitors.ato"

module BatteryMonitor:
    """Battery voltage divider for XIAO ADC.

    BAT+ --[200kΩ]--+-- to ADC
                    |
                    +-- 100nF -- GND
                    |
                    +--[200kΩ]-- GND

    Divider ratio = 0.5 → 4.2 V max input maps to 2.1 V at ADC (well within
    ESP32-C3 ADC1 range 0–3.1 V at default attenuation).
    """
    bat_plus = new ElectricPower    # raw battery rail (post-fuse)
    adc_out = new ElectricSignal    # to XIAO ADC pin

    r_top = new Resistor
    r_top.resistance = 200kohm +/- 1%
    r_top.package = "0402"
    r_top.lcsc = "C25745"

    r_bot = new Resistor
    r_bot.resistance = 200kohm +/- 1%
    r_bot.package = "0402"
    r_bot.lcsc = "C25745"

    c_filt = new Capacitor
    c_filt.capacitance = 100nF +/- 10%
    c_filt.package = "0402"
    c_filt.lcsc = "C1525"

    bat_plus.hv ~ r_top.p1
    r_top.p2 ~ adc_out.line
    adc_out.line ~ r_bot.p1
    r_bot.p2 ~ bat_plus.lv

    adc_out.line ~ c_filt.p1
    c_filt.p2 ~ bat_plus.lv

    adc_out.reference ~ bat_plus.lv
```

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "battery_monitor.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/io/battery_monitor.ato
git commit -m "Add BatteryMonitor module — 200k/200k divider with filter cap"
```

---

### Task 1.15: Write `io/expander.ato`

**Files:**
- Create: `elec/src/io/expander.ato`

TCA9534A 8-bit GPIO expander with the eight P0–P7 ports exposed as discrete signals, plus a 10 kΩ pull-up on the open-drain INT line.

- [ ] **Step 1: Write the module**

Create `elec/src/io/expander.ato`:

```ato
import ElectricPower, ElectricSignal, I2C from "generics/interfaces.ato"
import Resistor from "generics/resistors.ato"
import Capacitor from "generics/capacitors.ato"

# TI TCA9534APWR — 8-bit I²C GPIO expander, TSSOP-16
# Address 0x38 when A0/A1/A2 = GND, GND, GND
component TCA9534A:
    pin A0
    pin A1
    pin A2
    pin SDA
    pin SCL
    pin INT
    pin VCC
    pin GND
    pin P0
    pin P1
    pin P2
    pin P3
    pin P4
    pin P5
    pin P6
    pin P7
    package = "TSSOP-16_4.4x5mm_P0.65mm"
    lcsc = "C103079"
    designator_prefix = "U"

module Expander:
    """TCA9534A @ 0x38 with INT pull-up.

    Port allocation:
      P0  BTN_LEFT             (input, pulled up)
      P1  BTN_RIGHT            (input, pulled up)
      P2  AD8317_ENBL          (output, default low)
      P3  EPD_RST              (output, default low)
      P4  EPD_CS               (output, default high)
      P5  EPD_BUSY             (input)
      P6  LIS2MDL_DRDY         (input)
      P7  DRV2605L_ENABLE      (output, default low)
    """
    power3v3 = new ElectricPower
    i2c = new I2C
    int_signal = new ElectricSignal     # to XIAO D3 / EXP_INT

    # Port signals
    p0 = new ElectricSignal    # BTN_LEFT
    p1 = new ElectricSignal    # BTN_RIGHT
    p2 = new ElectricSignal    # AD8317_ENBL
    p3 = new ElectricSignal    # EPD_RST
    p4 = new ElectricSignal    # EPD_CS
    p5 = new ElectricSignal    # EPD_BUSY
    p6 = new ElectricSignal    # LIS2MDL_DRDY
    p7 = new ElectricSignal    # DRV2605L_ENABLE

    u7 = new TCA9534A

    # Decoupling
    c_dec = new Capacitor
    c_dec.capacitance = 100nF +/- 10%
    c_dec.package = "0402"
    c_dec.lcsc = "C1525"

    # INT pull-up — open-drain output requires external pull-up
    r_int = new Resistor
    r_int.resistance = 10kohm +/- 5%
    r_int.package = "0402"
    r_int.lcsc = "C25744"

    # ----- Wiring -----
    power3v3.hv ~ u7.VCC
    power3v3.lv ~ u7.GND

    # Address strap to GND for 0x38
    u7.A0 ~ power3v3.lv
    u7.A1 ~ power3v3.lv
    u7.A2 ~ power3v3.lv

    # Decoupling
    u7.VCC ~ c_dec.p1
    c_dec.p2 ~ power3v3.lv

    # I²C
    i2c.sda.line ~ u7.SDA
    i2c.scl.line ~ u7.SCL
    i2c.sda.reference ~ power3v3.lv
    i2c.scl.reference ~ power3v3.lv

    # INT with pull-up to 3V3_D
    int_signal.line ~ u7.INT
    int_signal.line ~ r_int.p1
    r_int.p2 ~ power3v3.hv
    int_signal.reference ~ power3v3.lv

    # Port pin connections
    p0.line ~ u7.P0
    p1.line ~ u7.P1
    p2.line ~ u7.P2
    p3.line ~ u7.P3
    p4.line ~ u7.P4
    p5.line ~ u7.P5
    p6.line ~ u7.P6
    p7.line ~ u7.P7
    p0.reference ~ power3v3.lv
    p1.reference ~ power3v3.lv
    p2.reference ~ power3v3.lv
    p3.reference ~ power3v3.lv
    p4.reference ~ power3v3.lv
    p5.reference ~ power3v3.lv
    p6.reference ~ power3v3.lv
    p7.reference ~ power3v3.lv
```

- [ ] **Step 2: Verify ato parses the file**

Run:
```bash
ato build aura 2>&1 | grep "expander.ato\|error" | head -10
```
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add elec/src/io/expander.ato
git commit -m "Add Expander module — TCA9534A @ 0x38 with INT pull-up"
```

---

### Task 1.16: Write `aura.ato` — top-level wiring

**Files:**
- Create: `elec/src/aura.ato`

The top-level module instantiates all leaf modules and wires them together via interfaces. This is the only file that should know about the global net topology; every leaf module knows only its local pins and interfaces.

- [ ] **Step 1: Write the top-level**

Create `elec/src/aura.ato`:

```ato
import ElectricPower, ElectricSignal, I2C from "generics/interfaces.ato"
import Resistor from "generics/resistors.ato"

import Battery from "power/battery.ato"
import Buck from "power/buck.ato"
import Rails from "power/rails.ato"
import XiaoC3 from "mcu/xiao_c3.ato"
import StrappingPullups from "mcu/pinmap.ato"
import Ad8317Detector from "rf/ad8317.ato"
import RfMatching from "rf/matching.ato"
import Antenna from "rf/antenna.ato"
import Magnetometer from "sensors/magnetometer.ato"
import Microphone from "sensors/microphone.ato"
import Drv2605l from "haptic/drv2605l.ato"
import EpaperFpc from "display/epaper_fpc.ato"
import Buttons from "io/buttons.ato"
import BatteryMonitor from "io/battery_monitor.ato"
import Expander from "io/expander.ato"

module Aura:
    """Top-level AURA EMF keychain — see docs/superpowers/specs/2026-05-08-*."""

    # -------- Module instances --------
    battery_block = new Battery
    buck_block    = new Buck
    rails_block   = new Rails
    mcu           = new XiaoC3
    straps        = new StrappingPullups
    rf_detector   = new Ad8317Detector
    rf_match      = new RfMatching
    antenna       = new Antenna
    mag           = new Magnetometer
    mic           = new Microphone
    haptic        = new Drv2605l
    display       = new EpaperFpc
    buttons       = new Buttons
    bat_mon       = new BatteryMonitor
    expander      = new Expander

    # -------- I²C bus pull-ups (4.7 kΩ to 3V3_D, near XIAO) --------
    r_pu_sda = new Resistor
    r_pu_sda.resistance = 4.7kohm +/- 5%
    r_pu_sda.package = "0402"
    r_pu_sda.lcsc = "C25804"

    r_pu_scl = new Resistor
    r_pu_scl.resistance = 4.7kohm +/- 5%
    r_pu_scl.package = "0402"
    r_pu_scl.lcsc = "C25804"

    # -------- Power tree --------
    # Battery → polyswitch → buck input
    battery_block.fused ~ buck_block.vin

    # Buck output is 3V3_D; feed mcu.power3v3, expander, sensors, haptic, display, buttons,
    # straps, and rails-input. Rails outputs 3V3_RF for AD8317.
    buck_block.vout ~ rails_block.in_3v3
    buck_block.vout ~ mcu.power3v3
    buck_block.vout ~ expander.power3v3
    buck_block.vout ~ mag.power3v3
    buck_block.vout ~ mic.power3v3
    buck_block.vout ~ haptic.power3v3
    buck_block.vout ~ display.power3v3
    buck_block.vout ~ buttons.power_ref
    buck_block.vout ~ straps.power3v3
    buck_block.vout ~ rf_match.power_ref   # for shunt termination GND

    # 3V3_RF feeds AD8317 only
    rails_block.out_3v3_rf ~ rf_detector.power3v3rf

    # Battery monitor taps post-polyswitch BAT+
    battery_block.fused ~ bat_mon.bat_plus

    # XIAO VBUS sense feeds buck Q1 gate
    mcu.vbus_sense ~ buck_block.vbus_sense

    # -------- I²C bus (D4/D5 from XIAO + pull-ups) --------
    mcu.i2c ~ mag.i2c
    mcu.i2c ~ haptic.i2c
    mcu.i2c ~ expander.i2c

    # I²C pull-ups — SDA to 3V3_D, SCL to 3V3_D
    mcu.i2c.sda.line ~ r_pu_sda.p1
    r_pu_sda.p2 ~ buck_block.vout.hv
    mcu.i2c.scl.line ~ r_pu_scl.p1
    r_pu_scl.p2 ~ buck_block.vout.hv

    # -------- SPI to e-paper (D9 SCK, D10 MOSI) --------
    mcu.spi ~ display.spi

    # SPI strap pull-up: D9 (SCK) — provided by StrappingPullups
    mcu.spi.sclk ~ straps.d9_strap

    # -------- I²S to microphone (D6 BCLK, D7 LRCLK, D8 DIN) --------
    mcu.i2s ~ mic.i2s

    # I²S DIN strap pull-up: D8 — provided by StrappingPullups
    mcu.i2s.din ~ straps.d8_strap

    # -------- Direct GPIO connections --------
    mcu.epd_dc ~ display.dc
    mcu.ad8317_vout_filt ~ rf_detector.vout_filtered
    mcu.bat_sense ~ bat_mon.adc_out
    mcu.exp_int ~ expander.int_signal

    # -------- Expander port wiring --------
    expander.p0 ~ buttons.btn_left
    expander.p1 ~ buttons.btn_right
    expander.p2 ~ rf_detector.enbl
    expander.p3 ~ display.rst
    expander.p4 ~ display.cs
    expander.p5 ~ display.busy
    expander.p6 ~ mag.drdy
    expander.p7 ~ haptic.enable

    # -------- RF chain --------
    antenna.feed ~ rf_match.ant_in
    rf_match.rfin_out ~ rf_detector.rfin

    # -------- LRA (motor pads) --------
    # The DRV2605L exposes lra as an ElectricPower; the actual motor is enclosure-mounted.
    # Plan 2 will draw the LRA pads on bottom side and route via vias.
    # No top-level wiring needed — haptic.lra is already complete locally.
```

- [ ] **Step 2: Run full ato build and verify ERC**

Run:
```bash
ato build aura 2>&1 | tee /tmp/ato_build.log | tail -60
```
Expected: build completes without parse errors. ERC may report warnings — these are acceptable if they describe known no-connect pins (DRV2605L IN_TRIG, DRV2605L NC, FPC NC pins, LIS2MDL INT, AD8317 may flag the dual-routing of INHI to RFIN). ERC errors must be zero.

If ERC errors appear, read each one and fix in the relevant module file. Common fixes:
- "Pin not connected" → add explicit no-connect (atopile syntax: `signal nc_<pin> ~ <module>.<pin>` and leave `nc_<pin>` dangling), or actually wire it
- "Multiple drivers on net X" → check that two modules don't both output to the same signal
- "Power net has no driver" → check the power tree is actually connected through to the source

- [ ] **Step 3: Commit**

```bash
git add elec/src/aura.ato
git commit -m "Add top-level Aura module wiring all leaf modules"
```

---

### Task 1.17: Generate KiCad netlist and BOM

**Files:**
- Read-only: `build/aura/aura.kicad_netlist`, `build/aura/aura_bom.csv`

- [ ] **Step 1: Build with KiCad output**

Run:
```bash
ato build aura
ls -la build/aura/
```
Expected: `build/aura/` contains a netlist file (extension `.net` or `.kicad_netlist` depending on atopile version) and `aura_bom.csv`.

If the BOM isn't auto-generated, check the atopile docs for the correct command — likely `ato build aura --output bom` or similar:
```bash
ato build --help
```

- [ ] **Step 2: Inspect netlist for sanity**

Run:
```bash
head -50 build/aura/*.net 2>/dev/null || head -50 build/aura/*.kicad_netlist
wc -l build/aura/*.net 2>/dev/null || wc -l build/aura/*.kicad_netlist
```
Expected: file is ≥ 200 lines (lots of nets), contains `(comp ...)` entries for U1 (XIAO), U2 (AD8317), U3 (DRV2605L), U4 (LIS2MDL), U5 (ICS-43434), U6 (TPS62840), U7 (TCA9534A).

Run:
```bash
grep -c '(comp ' build/aura/*.net 2>/dev/null || grep -c '(comp ' build/aura/*.kicad_netlist
```
Expected: total component count matches what you'd expect (roughly: 7 ICs + 1 NFET + 2 buttons + 1 polyswitch + 1 inductor + 1 ferrite + 1 FPC + ~30 caps + ~10 resistors = ~55 parts).

- [ ] **Step 3: Inspect BOM**

Run:
```bash
head -20 build/aura/aura_bom.csv
wc -l build/aura/aura_bom.csv
```
Expected: header row + ~55 component rows. Verify the columns include reference, value, package, LCSC C-number.

- [ ] **Step 4: No commit** — `build/` is gitignored. The artifacts are reproducible from the source.

---

### Task 1.18: Write a smoke-test script for build reproducibility

**Files:**
- Create: `scripts/build-check.sh`

A small script that any developer (or CI) can run to confirm the schematic still builds clean.

- [ ] **Step 1: Write the script**

Create `scripts/build-check.sh`:

```bash
#!/usr/bin/env bash
# Smoke test — rebuilds AURA schematic, fails on ERC errors or missing artifacts.
# Run: ./scripts/build-check.sh
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

echo "==> Cleaning previous build"
rm -rf build/

echo "==> Running ato build aura"
ato build aura

NETLIST=$(find build/aura -maxdepth 1 -type f \( -name '*.net' -o -name '*.kicad_netlist' \) | head -1)
BOM="build/aura/aura_bom.csv"

if [[ ! -f "$NETLIST" ]]; then
    echo "FAIL: netlist not produced"
    exit 1
fi

if [[ ! -f "$BOM" ]]; then
    echo "FAIL: BOM not produced"
    exit 1
fi

NET_LINES=$(wc -l < "$NETLIST")
BOM_LINES=$(wc -l < "$BOM")
COMP_COUNT=$(grep -c '(comp ' "$NETLIST" || true)

echo "==> Build artifacts:"
echo "    netlist: $NETLIST ($NET_LINES lines)"
echo "    BOM:     $BOM ($BOM_LINES lines)"
echo "    components: $COMP_COUNT"

if [[ "$COMP_COUNT" -lt 40 ]]; then
    echo "FAIL: component count $COMP_COUNT is suspiciously low (expected ~55)"
    exit 1
fi

echo "==> OK"
```

- [ ] **Step 2: Make it executable and test**

Run:
```bash
chmod +x scripts/build-check.sh
./scripts/build-check.sh
```
Expected: ends with `==> OK`. Any failure here means the schematic regressed.

- [ ] **Step 3: Commit**

```bash
git add scripts/build-check.sh
git commit -m "Add build-check.sh smoke test for schematic reproducibility"
```

---

### Task 1.19: Final review checklist + commit summary

**Files:**
- Create: `docs/superpowers/specs/plan-1-completion-notes.md`

A short notes file capturing what's verified at end of Plan 1 and what's deferred to Plan 2/3. Useful for the subagent that picks up Plan 2 with no prior context.

- [ ] **Step 1: Write the completion notes**

Create `docs/superpowers/specs/plan-1-completion-notes.md`:

```markdown
# Plan 1 completion notes — AURA schematic

## Verified at end of Plan 1
- atopile schematic builds clean (`./scripts/build-check.sh` passes)
- All 14 leaf modules + top-level instantiate without parse errors
- Power tree connects: Battery → polyswitch → Buck → Rails → AD8317 (3V3_RF) and all digital loads (3V3_D)
- !VBUS gating wired: XIAO VBUS pad → Q1 gate → buck EN
- I²C bus has 3 devices (mag, haptic, expander) at distinct addresses 0x1E, 0x5A, 0x38
- Expander P0–P7 fully allocated per spec (no spare port)
- Strap pull-ups present on D8 and D9
- I²C 4.7 kΩ pull-ups present on SDA and SCL
- BOM produced with LCSC C-numbers for every part

## Known artifacts (acceptable warnings)
- DRV2605L IN_TRIG, NC pins: dangling — handled in Plan 2 with KiCad no-connect markers
- FPC J1 NC pins (P10, P11, P18, P19, P20, P21): dangling — same handling
- LIS2MDL INT pin: dangling — same handling
- AD8317 INHI net pulled to RFIN: per datasheet, expected single-ended drive

## Deferred to Plan 2
- Vendor Seeed XIAO ESP32-C3 KiCad library
- Footprint verification against datasheets (LFCSP exposed pad, FPC pin 1, etc.)
- KiCad project bootstrap with JLC04161H-7628 stackup
- DRC profile configuration
- Component placement per spec §8 floorplan
- Mechanical features (40 × 32 mm outline, mounting holes, keyring hole)

## Deferred to Plan 3
- Antenna meander geometry (drawn in pcbnew)
- 50 Ω microstrip traces
- All routing (RF, power, digital, I²C, SPI, I²S)
- Copper pours on L2/L3/L4 with antenna keep-out
- Stitching vias
- Pre-fab outputs (Gerbers, drill, STEP, CPL, design notes)

## Open questions surfaced during P1
(populate as they come up during execution; otherwise leave empty)
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/plan-1-completion-notes.md
git commit -m "Document Plan 1 completion state and Plan 2/3 hand-off"
```

- [ ] **Step 3: Confirm clean tree and final state**

Run:
```bash
git status
git log --oneline -25
./scripts/build-check.sh
```
Expected:
- `git status` shows clean working tree
- `git log` shows ~20 commits since "Add AURA EMF-keychain PCB design spec"
- `build-check.sh` ends with `==> OK`

This concludes Plan 1.

---

## Self-review checklist (already run before publishing this plan)

The following items were checked and addressed inline; documented here so a reviewer can verify quickly:

- **Spec coverage:** Every locked decision in the spec — power tree, !VBUS gating, expander port allocation, strap pull-ups, I²C addresses, RF decoupling, FPC boost caps, battery divider, button debounce/ESD — has a task. Antenna geometry, KiCad stackup, footprints, placement, routing, and pours are intentionally deferred to Plans 2 and 3.
- **Placeholders:** No "TBD" / "implement later" / "add error handling" / "similar to Task N" anywhere. Every step has the actual code or commands the engineer needs.
- **Type consistency:** Module names match across imports and instantiations. `Aura` is the entry, `Battery`/`Buck`/`Rails`/`XiaoC3`/etc. are the leaf modules. `ElectricPower` (`.hv`/`.lv`) used everywhere for power; `ElectricSignal` (`.line`/`.reference`) for single-ended signals; `I2C` (`.sda.line`/`.scl.line`), `SPI` (`.sclk.line`/`.mosi.line`), `I2S` (`.bclk.line`/`.lrclk.line`/`.din.line`) for buses.
- **Scope:** Single deliverable — KiCad netlist + BOM, ERC clean. Each task produces a self-contained commit. ~22 commits total.
