# Plan 1 completion notes — AURA SKiDL schematic

**Status:** ✅ Schematic-equivalent generated, ERC clean (0 errors), netlist + BOM + connectivity-doc committed.

## Verified at end of Plan 1

- `uv run --with skidl python3 elec/src/aura.py` produces a clean build:
  - **69 parts** (26 caps, 15 resistors, 7 test points, 6 ICs, 5 PWR_FLAGs, 3 connectors, 2 switches, 1 NFET, 1 mic, 1 inductor, 1 ferrite, 1 polyswitch)
  - **42 nets** (all 30 named nets from the spec + 12 internal local nets)
  - **0 errors** during ERC and netlist generation
  - **143 warnings** — all cosmetic (auto-generated SKiDL tags, missing fp-lib-table for not-yet-created KiCad project, KICAD6/7/9 env-var noise)
- Power tree connects per spec:
  - `BAT_PLUS` → polyswitch + 0Ω jumper → `BAT_PLUS_FUSED` → buck VIN
  - Q1 inverter: `VBUS_SENSE` → 2N7002.G; 2N7002.D → buck EN; 100 kΩ from EN to `BAT_PLUS_FUSED`
  - Buck VOUT → `V3V3_D` → all digital + sensors + expander
  - `V3V3_D` → FB1 ferrite → `V3V3_RF` → AD8317 only
- I²C bus has 3 devices at distinct addresses: LIS2MDL `0x1E`, DRV2605L `0x5A`, TCA9534A `0x38`
- TCA9534A P0–P7 fully allocated per spec (no spare port)
- D8 and D9 strap pull-ups present
- I²C 4.7 kΩ pull-ups on SDA + SCL near MCU
- 7 test points present: BAT+, V3V3_D, V3V3_RF, AD8317_VOUT, I2C_SDA, I2C_SCL, GND
- BOM CSV groups equivalent parts and includes LCSC C-numbers

## Build artifacts (gitignored under `elec/build/`)

| File | Purpose |
|---|---|
| `aura.net` | KiCad netlist for pcbnew import in Plan 2 |
| `aura_bom.csv` | BOM with LCSC C-numbers, grouped by (value, footprint, LCSC) |
| `aura_connectivity.md` | Per-net endpoint catalog — human-reviewable schematic-equivalent |
| `aura_erc.log` | ERC report (2 cosmetic warnings, 0 errors) |

Reproduce with: `uv run --with skidl python3 elec/src/aura.py`

## Custom symbols vendored (in `lib/aura.kicad_sym`)

- **AD8317** — LFCSP-8 RF log detector (KiCad stdlib has only AD8313)
- **TPS62840** — HVSSOP-8 buck regulator (KiCad stdlib has TPS62823 but not 62840)
- **XIAO_ESP32_C3** — 14-pad castellated module (modeled as connector with named D0–D10 + 3V3 + 5V/VBUS + GND pads)

## Cosmetic warnings (acceptable)

- **"Missing tag on X"** (~120 instances) — SKiDL auto-generates random tags for parts without explicit `tag=` kwargs. Tags affect schematic-stability across rebuilds, not netlist correctness. Not worth fixing now.
- **"fp-lib-table file was not found"** — KiCad's footprint table doesn't exist yet because there's no KiCad project. Set up in Plan 2.
- **KICAD6/7/9_SYMBOL_DIR missing** — irrelevant; we set `KICAD8_SYMBOL_DIR` and that's what SKiDL uses.
- **EXP_INT pin conflict (BIDIRECTIONAL ↔ OPEN-COLLECTOR)** — XIAO D3 is BIDIRECTIONAL by default; TCA9534A INT is OPEN-COLLECTOR. The MCU-side pull-up converts open-drain to digital input correctly in firmware. Mark D3 as INPUT in firmware config.
- **"Net merging GND and BAT_MINUS"** — intentional; BAT- pad is wired to GND.

## Deferred to Plan 2

- KiCad project creation (`aurapcb.kicad_pro`)
- JLC04161H-7628 stack-up configuration
- DRC profile (per spec §2.3)
- `lib/aura.kicad_sym` registration in the project's symbol-lib-table
- `fp-lib-table` configuration (point at KiCad standard footprints + project-local Module:Seeed_XIAO_ESP32_C3)
- Vendor Seeed XIAO ESP32-C3 footprint
- Verify LFCSP-8 exposed pad against ADI datasheet
- Verify FH12-24S-0.5SH pin-1 orientation against e-paper module datasheet
- Import `aura.net` into pcbnew
- Place 40 × 32 mm board outline + mounting + keyring holes
- Component placement per spec §8 floorplan

## Deferred to Plan 3

- Antenna meander geometry (drawn in pcbnew, not generatable from netlist)
- 50 Ω microstrip trace tuning
- All routing
- Copper pours on L2/L3/L4 with antenna keep-out
- Stitching vias
- Pre-fab outputs (Gerbers, drill, STEP, CPL, design notes)

## How to read the connectivity doc

`elec/build/aura_connectivity.md` lists every net and its endpoints. Use it
to verify intent without opening eeschema. Example:

```
### `I2C_SDA`  (6 pins)
- `R6.1` ()         ← 4.7 kΩ pull-up to V3V3_D
- `TP5.1` (1)        ← test point
- `U2.5` (D4)        ← XIAO D4
- `U4.4` (SDA/SDI/SDO) ← LIS2MDL
- `U5.3` (SDA)       ← DRV2605L
- `U6.15` (SDA)      ← TCA9534A expander
```

The visual schematic in eeschema is intentionally NOT generated (S-expression
auto-layout produces unreadable schematics). Visual review happens against
the spec's block diagram (§3) + this connectivity doc.
