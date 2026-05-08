# AURA — EMF-Sensing Keychain PCB Design Spec

**Date:** 2026-05-08
**Project:** AURA (codename) — 4-layer rigid PCB, keychain form factor, EMF detector + magnetometer + I²S microphone + e-paper UI + haptic
**Design tooling:** **SKiDL (Python netlist generator) → KiCad 10 (layout)** — see Toolchain Amendment below
**Fab target:** JLCPCB JLC04161H-7628 4-layer ENIG, 0.8 mm
**Working mode:** full generation (Claude produces SKiDL Python sources, KiCad project skeleton, custom symbols, netlist, BOM, layout, connectivity doc, design notes; user reviews at phase gates)
**Scope:** complete schematic-equivalent (netlist + connectivity doc, no viewable `.kicad_sch`) + placed/routed/poured layout + 3D STEP + fab outputs

## Toolchain Amendment (2026-05-08, post-spec-approval)

The original spec named atopile as the schematic-as-code tool. During execution we discovered that atopile 0.15.7's IC declaration patterns don't match the older docs we worked from, and the package registry doesn't yet have entries for the AD8317, TPS62840, or XIAO ESP32-C3 module — meaning we'd need to wrangle custom IC declarations against an evolving toolchain. We pivoted to:

**SKiDL** (Python library, mature, KiCad-native) generates a KiCad netlist directly from Python source. Modules from KiCad 10's standard symbol libraries cover most ICs directly (LIS2MDL, TCA9534, DRV2605LDGS, ICS-43434, 2N7002, etc.); two custom symbols are vendored into a project library (`lib/aura.kicad_sym`) for AD8317 and TPS62840, plus the XIAO ESP32-C3 module is modeled as a 14-pin generic connector with named labels.

**No viewable `.kicad_sch`** — the schematic-equivalent is delivered as a comprehensive **connectivity-doc Markdown** that catalogs every net and its endpoints. Pcbnew imports the netlist for layout. The visual design intent lives in this spec's block diagram (§3) and floorplan (§8); review happens against the spec + connectivity doc, not against an eeschema view.

This pivot affects only sections 7 (Schematic hierarchy) and 10 (Phase plan) — every other locked decision (pin map, power tree, RF rules, layout floorplan, antenna design, DRC profile, BOM, risk list) carries through unchanged.



---

## 1. Overview

AURA is a battery-powered keychain device that senses electromagnetic activity in three modalities and presents readings on an e-paper display:

- **RF energy** (500 MHz – 3 GHz) via an Analog Devices AD8317 logarithmic detector fed by an on-board meander antenna
- **Static / quasi-static magnetic field** via an ST LIS2MDL three-axis magnetometer
- **Audible-band EMI acoustics** via an InvenSense ICS-43434 I²S MEMS microphone

User interaction is through two tactile buttons, an e-paper display, and a haptic LRA. Power comes from a single LiPo cell charged via the XIAO ESP32-C3's USB-C; the system runs on a TPS62840 buck regulator with a !VBUS-gated enable so the buck shuts down whenever USB-C is plugged in (eliminating contention with the XIAO's onboard LDO).

The design is hand-soldered as a single prototype, then scaled to 1 K production units assembled by JLCPCB.

## 2. Constraints

### 2.1 Mechanical

| Item | Value |
|---|---|
| Outline | 40 × 32 mm rectangular, 3 mm corner radius (landscape) |
| Thickness | 0.8 mm ± 10 % |
| Layer count | 4 |
| Surface finish | ENIG |
| Mounting holes | 2 × M1.6, top corners, 2 mm from edge |
| Keyring hole | ⌀ 3 mm, top-center, 2 mm from top edge, 2 mm copper clearance ring |
| Single-sided assembly | All ICs on top side; battery + LRA pads on bottom |
| Z budget | 7 mm enclosure total: 3 mm battery below, 0.8 mm PCB, ≤ 3 mm components above |

### 2.2 Electrical

| Item | Value |
|---|---|
| Battery | 1S LiPo, 3.0–4.2 V, ~200 mAh assumed |
| USB-C charging | Handled on-module by the XIAO ESP32-C3 |
| Main rail | 3V3_D digital + sensors |
| RF rail | 3V3_RF, AD8317 only, isolated by FB1 ferrite |
| Active sense current | ~108 mA (XIAO + AD8317 + sensors) |
| Deep-sleep current | ~25 µA |
| Avg @ 1 s wake / 30 s | ~3.6 mA → ~55 h continuous on 200 mAh |

### 2.3 Fab DRC (JLCPCB 4-layer ENIG, 0.8 mm)

| Rule | Value |
|---|---|
| Min trace / space | 0.10 mm (relaxed from JLC's 0.0889 mm minimum for margin) |
| Default trace width | 0.15 mm (digital, I²C, SPI, I²S) |
| RF 50 Ω microstrip | 0.30 mm |
| Power trace (3V3_D) | 0.40 mm |
| Power trace (BAT+) | 0.50 mm |
| Min via | 0.30 mm hole / 0.60 mm pad |
| RF stitching via | 0.30 mm hole / 0.50 mm pad |
| Min annular ring | 0.10 mm |
| Edge clearance | 0.40 mm copper-to-edge |
| Antenna keep-out | **5 mm halo on ALL four layers**, no copper |

### 2.4 Stack-up (JLC04161H-7628)

| Layer | Function | Copper | Below |
|---|---|---|---|
| L1 (top) | Signal + components, 50 Ω microstrip ref to L2 | 1 oz (35 µm) | 0.16 mm 7628 prepreg |
| L2 | Solid GND plane — uninterrupted under RF | 0.5 oz (17 µm) | 0.40 mm core |
| L3 | Power plane: 3V3_D / 3V3_RF islands joined at FB1 | 0.5 oz (17 µm) | 0.16 mm 7628 prepreg |
| L4 (bottom) | Signal + battery pads + LRA pads | 1 oz (35 µm) | — |

50 Ω microstrip on L1 → L2 with 0.16 mm prepreg, FR-4 Dk ≈ 4.3 → trace width **0.30 mm**. Antenna trace and the AD8317 RFIN trace use this width.

## 3. Architecture

### 3.1 System block diagram

```
                       ┌────────────────────────────────────┐
                       │         XIAO ESP32-C3              │
   ANT (meander) ─CIN─►│ ADC1_4  ◄─ AD8317 VOUT (filtered)  │
   500 MHz–3 GHz       │ ADC1_3  ◄─ BAT_SENSE (200k/200k)   │
        │              │ ADC1_2  ◄─ EPD_DC                  │  on-module
        ▼              │ GPIO5   ──► EXP_INT (open-drain)   │  WiFi/BT
     AD8317 ──RFIN     │ I²C(6,7) ◄──► bus to mag/hap/expdr │  antenna
     log det           │ SPI(9,10)──► e-paper FPC           │  → right
        │ ENBL         │ I²S(8,21,20) ◄── ICS-43434 mic     │
        ▼              └────────────────────────────────────┘
   TCA9534A ◄──I²C─────┘
   8-bit expander
   ├ P0/P1: BTN_L / BTN_R
   ├ P2:    AD8317_ENBL
   ├ P3:    EPD_RST
   ├ P4:    EPD_CS
   ├ P5:    EPD_BUSY
   ├ P6:    LIS2MDL_DRDY
   └ P7:    DRV2605L_ENABLE
```

### 3.2 Power tree

```
VBUS (USB-C, 5 V) ──► XIAO charger ──► BAT+ (LiPo, 3.0–4.2 V)
                  └─► XIAO LDO ──► 3V3_D    (when USB-C in)

BAT+ ──► F1 polyswitch (1A, ‖ 0Ω jumper) ──► BAT+_FUSED
                                                    │
                                                    ├─► XIAO BAT pin
                                                    │
                            !VBUS gate              ▼
                            (Q1 + 100k)         TPS62840 buck
                                ▲                EN ← !VBUS
                            VBUS ┘                MODE → GND (PFM)
                                                 L1 = 2.2 µH
                                                 C_IN 10 µF, C_OUT 10 µF + 100 nF
                                                    │
                                                    ▼ 3V3_D
                                                    │
                                       ┌────────────┴──────────────┐
                                       ▼                            ▼
                              All digital + sensors         FB1 BLM18PG471SN1D
                              (XIAO, mag, mic, hap,                ▼ 3V3_RF
                               expander, e-paper boost,        AD8317
                               I²C pull-ups)                   (sole load)
```

The Q1 inverter ensures only one of the two 3V3 sources drives the rail at a time:
- USB-C plugged in → VBUS high → buck disabled → XIAO LDO drives 3V3_D
- USB-C unplugged → VBUS = 0 → buck enabled → BAT+ → 3V3_D
- 3V3_RF is always derived from 3V3_D through FB1, regardless of source

## 4. Component selection

### 4.1 Active devices

| Ref | Part | Package | I²C addr / Notes |
|---|---|---|---|
| **U1** | XIAO ESP32-C3 module (Seeed) | castellated 14-pad daughterboard | 21 × 17.5 × ~3 mm |
| **U2** | AD8317ACPZ-R7 | LFCSP-8, 3 × 2 mm | RF log detector, 1 MHz–10 GHz |
| **U3** | DRV2605LDGSR | VSSOP-10 | I²C `0x5A`, ENABLE on TCA9534A P7 |
| **U4** | LIS2MDLTR | LGA-12, 2 × 2 mm | I²C `0x1E`, DRDY on TCA9534A P6 |
| **U5** | ICS-43434 | LGA-6, 3 × 4 mm | I²S, L/R = GND (left ch) |
| **U6** | TPS62840DGRR | HVSSOP-8 | 3.3 V buck, 60 nA Iq, EN ← !VBUS |
| **U7** | TCA9534A | TSSOP-16 | 8-bit I²C GPIO expander, `0x38` (A0–A2 = GND) |
| **Q1** | 2N7002 | SOT-23 | NFET inverter for !VBUS gating |

### 4.2 Passives & misc (key items)

| Ref | Part / Value | Notes |
|---|---|---|
| L1 | DFE201610E-2R2M (Murata, 2.2 µH, 1 A sat) | TPS62840 buck inductor |
| FB1 | BLM18PG471SN1D | 470 Ω @ 100 MHz, 1 A — 3V3_D ↔ 3V3_RF |
| F1 | MF-FSMF110X | 1 A polyswitch, 0805 |
| F1_BYP | 0Ω 0805 | jumper across F1 for sleep-Iq measurement |
| CIN_RF | 1 nF 0402 X7R | AC-couple antenna to AD8317 RFIN |
| R_TERM | 52.3 Ω 0402 1% | shunt termination on antenna side of CIN |
| R_TUNE | DNP 0402 footprint | populate post-VNA if S11 > –6 dB |
| C_LPF_RF | 100 nF 0402 + 1 kΩ 0402 | RC low-pass on AD8317 VOUT before XIAO ADC |
| R_PU_SDA / SCL | 4.7 kΩ 0402 each | I²C pull-ups to 3V3_D, near XIAO |
| R_DIV_BAT | 200 kΩ + 200 kΩ 0402 | battery sense divider |
| C_DIV_BAT | 100 nF 0402 | divider midpoint filter |
| C_DECOUPLE_VPOS | 100 nF + 10 nF + 1 nF 0402 X7R | AD8317 VPOS, within 1 mm |
| C_BOOST_EPD | 4 × 1 µF 0402 X5R | e-paper boost circuit, within 5 mm of FPC |
| BTN1, BTN2 | C&K PTS815 | 4 × 3 mm SMD tactile |
| C_DEB | 100 nF 0402 | debounce per button |
| R_ESD_BTN | 1 kΩ 0402 | series ESD protection per button |
| J1 | Hirose FH12-24S-0.5SH | 24-pin 0.5 mm pitch FPC, top contact |
| M1 | LRA, 8 × 3 mm coin, 5 mm pad pitch | mounted enclosure-side via VHB |
| ANT1 | meander trace, see §6 | not a discrete part |

### 4.3 Test points

| Net | TP designator | Pad |
|---|---|---|
| BAT+ | TP1 | 0.8 mm round |
| 3V3_D | TP2 | 0.8 mm round |
| 3V3_RF | TP3 | 0.8 mm round |
| AD8317_VOUT | TP4 | 0.8 mm round |
| I2C_SDA | TP5 | 0.8 mm round |
| I2C_SCL | TP6 | 0.8 mm round |
| GND | TP7 | 0.8 mm round (single GND TP) |

## 5. Pin map (locked)

### 5.1 XIAO ESP32-C3 (U1)

| XIAO pin | GPIO | ADC ch | Net | Function | Note |
|---|---|---|---|---|---|
| D0 | GPIO2 | ADC1_2 | `EPD_DC` | E-paper data/command select | strap pin: idle-high OK |
| D1 | GPIO3 | ADC1_3 | `BAT_SENSE` | battery voltage divider midpoint | through 200k/200k + 100n |
| D2 | GPIO4 | ADC1_4 | `AD8317_VOUT_FILT` | RF detector output | RC LPF at AD8317 side |
| D3 | GPIO5 | ADC1_5 | `EXP_INT` | TCA9534A interrupt | open-drain, pulled up |
| D4 | GPIO6 | — | `I2C_SDA` | I²C bus | 4.7 kΩ to 3V3_D |
| D5 | GPIO7 | — | `I2C_SCL` | I²C bus | 4.7 kΩ to 3V3_D |
| D6 | GPIO21 | — | `I2S_BCLK` | I²S bit clock | repurposed from U0TXD |
| D7 | GPIO20 | — | `I2S_LRCLK` | I²S word select | repurposed from U0RXD |
| D8 | GPIO8 | — | `I2S_DIN` | I²S data in (mic → MCU) | strap: 10 kΩ to 3V3_D |
| D9 | GPIO9 | — | `SPI_SCK` | SPI clock | strap: 10 kΩ to 3V3_D |
| D10 | GPIO10 | — | `SPI_MOSI` | SPI data | — |
| 3V3 | — | — | `3V3_D` | power feed (overrides XIAO LDO when buck on) | |
| 5V/VBUS | — | — | `VBUS_SENSE` | high-impedance sense input to Q1 gate (no current draw on main board) | XIAO USB-C feeds its onboard charger and LDO; main board taps VBUS only for the !VBUS gating logic |
| GND (×4) | — | — | `GND` | ≥ 4 vias to L2 | |

**Strapping notes:** GPIO 2, 8, 9 are boot strapping pins on ESP32-C3.
- GPIO2 (D0 / EPD_DC): driven by MCU, idle high, default e-paper DC value during boot. ✓
- GPIO8 (D8 / I2S_DIN): input from ICS-43434 SDOUT; high-Z when L/R clock is idle. **10 kΩ pull-up to 3V3_D required**.
- GPIO9 (D9 / SPI_SCK): SPI mode 0 idles low, conflicts with strap-must-be-high. **10 kΩ pull-up to 3V3_D required**; the SPI master overrides during operation.

**UART consequence:** D6 / D7 default to U0TXD / U0RXD. We're using them for I²S, so no hardware UART is exposed on the breakout. Programming and debug serial go through the XIAO's USB-C (native USB-CDC).

### 5.2 TCA9534A (U7) port allocation

| Port | Direction | Net | Function |
|---|---|---|---|
| P0 | input, pulled up | `BTN_LEFT` | left tactile switch (one side to GND) |
| P1 | input, pulled up | `BTN_RIGHT` | right tactile switch |
| P2 | output, default low | `AD8317_ENBL` | high to enable RF detector (~22 mA) |
| P3 | output, default low | `EPD_RST` | held low through MCU init, then high |
| P4 | output, default high | `EPD_CS` | active-low SPI chip select for e-paper |
| P5 | input | `EPD_BUSY` | e-paper busy flag |
| P6 | input | `LIS2MDL_DRDY` | magnetometer data-ready interrupt |
| P7 | output, default low | `DRV2605L_ENABLE` | high to enable haptic IC |

A0–A2 strapped to GND → I²C address `0x38`. INT pulled up to 3V3_D via 10 kΩ; routed to XIAO `EXP_INT` (D3). 100 nF decoupling within 1 mm of VCC.

### 5.3 I²C bus inventory

All on 3V3_D, 400 kHz:

| Address | Device | Decoupling |
|---|---|---|
| `0x1E` | LIS2MDLTR magnetometer | 100 nF at VDD + 100 nF at VDDIO + 100 nF C0G across Cap pin to GND |
| `0x38` | TCA9534A GPIO expander | 100 nF at VCC |
| `0x5A` | DRV2605LDGSR haptic | 1 µF + 100 nF at VDD |

Bus pull-ups: 4.7 kΩ each on SDA, SCL to 3V3_D, placed near the XIAO module (the bus master).

## 6. Antenna design

### 6.1 Topology

End-fed meander monopole referenced to the L2 ground plane.

| Parameter | Value |
|---|---|
| Type | Meander monopole, 4 hairpins |
| Trace width | 0.30 mm (50 Ω microstrip on 0.16 mm prepreg) |
| Total electrical length | ~25 mm (~λ/4 at 1.5 GHz center) |
| Bounding box | 12 × 4 mm |
| Coverage | 500 MHz – 3 GHz, S11 best near center, > –6 dB acceptable for detector use |
| Keep-out | 5 mm halo on **all four layers**, no copper |
| Feed | 50 Ω microstrip ≤ 5 mm to AD8317 RFIN, AC-coupled via `CIN` (1 nF) |
| Termination | 52.3 Ω 0402 shunt to GND on antenna side of `CIN` (per ADI Figure 38) |
| Tuning | DNP 0402 footprint between feed and shunt — populate 1.0–4.7 pF if VNA S11 is poor |

### 6.2 Validation

VNA S11 sweep is **required** on the first prototype build before declaring the antenna acceptable. Acceptance criteria: S11 < –6 dB across at least 700 MHz – 2.6 GHz. If failing, populate the tune-stub footprint with discrete capacitance per the sweep.

### 6.3 Coexistence risk

The XIAO ESP32-C3 has an onboard 2.4 GHz BT/WiFi chip antenna. The AD8317 is broadband 1 MHz – 10 GHz, so its detection path includes the band the XIAO transmits in. **Firmware must time-multiplex** — never sample AD8317 while WiFi/BLE transmission is active. Layout mitigation: orient the XIAO antenna ~180° from the AD8317 antenna (XIAO antenna points right, AD8317 antenna points up-left), but expect ~20 dB residual coupling.

## 7. Schematic hierarchy (SKiDL — see Toolchain Amendment)

```
aurapcb/
├── lib/
│   └── aura.kicad_sym                 # custom symbols vendored locally:
│                                       #   - AD8317 (LFCSP-8 RF log detector)
│                                       #   - TPS62840 (HVSSOP-8 buck)
│                                       #   - XIAO_ESP32_C3 (14-pad module abstraction)
├── elec/
│   ├── src/
│   │   ├── __init__.py
│   │   ├── aura.py                    # top-level — instantiates leaf modules + ERC + netlist gen
│   │   ├── nets.py                    # named global nets (BAT_PLUS, V3V3_D, V3V3_RF, GND)
│   │   ├── power/
│   │   │   ├── battery.py             # BAT± + polyswitch + 0Ω jumper
│   │   │   ├── buck.py                # TPS62840 + L1 + caps + Q1 !VBUS gate
│   │   │   └── rails.py               # FB1 split between 3V3_D and 3V3_RF
│   │   ├── mcu/
│   │   │   └── xiao_c3.py             # XIAO ESP32-C3 module + pinmap + strap pull-ups
│   │   ├── rf/
│   │   │   ├── ad8317.py              # detector + 100n‖10n‖1n decoupling + RC LPF
│   │   │   └── matching.py            # CIN 1nF + 52.3Ω shunt + DNP tune stub + antenna feed
│   │   ├── sensors/
│   │   │   ├── magnetometer.py        # LIS2MDL @ 0x1E + caps + DRDY
│   │   │   └── microphone.py          # ICS-43434 + caps + I2S routing + L/R-tied-low
│   │   ├── haptic/
│   │   │   └── drv2605l.py            # @ 0x5A + LRA pads + ENABLE from expander P7
│   │   ├── display/
│   │   │   └── epaper_fpc.py          # 24-pin FH12-24S-0.5SH + 4 × 1µF boost caps
│   │   └── io/
│   │       ├── buttons.py             # 2 × PTS815 + 100n debounce + 1k ESD
│   │       ├── battery_monitor.py     # 200k/200k divider + 100n filter
│   │       └── expander.py            # TCA9534A @ 0x38 + 100n + INT pull-up
│   └── build/                          # generated, gitignored
│       ├── aura.net                    # KiCad netlist (pcbnew imports this)
│       ├── aura_bom.csv                # BOM with LCSC C-numbers
│       └── aura_connectivity.md        # human-reviewable net catalog (per-net endpoint listing)
├── layout/
│   └── aura.kicad_pcb                  # imported from aura.net; routing + pours hand-tuned
└── pyproject.toml                      # uv-managed Python deps (skidl)
```

Each leaf `.py` module exports a single function `build()` that takes the named global nets it needs and instantiates the components. `aura.py` calls all of them, then runs SKiDL's `ERC()` (electrical rules check), `generate_netlist()`, `generate_bom()`, and a custom `generate_connectivity_doc()` that walks the net graph and writes a per-net endpoint table to Markdown for human review.

## 8. Layout floorplan

Coordinates are in millimeters from the bottom-left corner; all dimensions approximate, pixel-precise placement happens in KiCad.

### 8.1 Top side (L1)

| Zone | x-range | y-range | Contents |
|---|---|---|---|
| Antenna keep-out | 0.5–17 | 22–32 | Meander 12 × 4 mm at top, 5 mm halo, NO copper any layer |
| Keyring + clearance | 18–25 | 25–31 | ⌀ 3 mm hole, 2 mm copper-clearance ring |
| FPC J1 | 26–39 | 26.5–32 | 24-pin FPC, top contact |
| FPC boost caps | 27.5–35 | 23–25 | 4 × 1 µF 0402, ≤ 5 mm from FPC |
| AD8317 + match | 16–21 | 19–22 | LFCSP-8 + decoupling + termination + RFIN routing ≤ 5 mm |
| TPS62840 + L1 | 1.5–7.5 | 14.5–19 | tight buck input loop < 5 mm² |
| Q1 + !VBUS gate | 1.5–7.5 | 11.5–14 | 2N7002 + 100 kΩ pull |
| TCA9534A | 8–13.5 | 14.5–18 | I²C expander |
| I²C pull-ups | 8–13.5 | 11.5–13 | 4.7 kΩ × 2 |
| LIS2MDL | 1.5–6.5 | 4–8 | ≥ 8 mm from LRA, antenna, BAT FETs |
| ICS-43434 + port | 7–12.5 | 4–8 | acoustic port ⌀ 1 mm through PCB |
| FB1 + transition | 13–17 | 4.5–6.5 | single-point 3V3_D → 3V3_RF |
| DRV2605L | 18–23.5 | 1.5–4.5 | drives LRA on bottom side via vias |
| BAT-mon + TPs | 24.5–32.5 | 1.5–4.5 | divider + 7 test points |
| BTN_LEFT | 1.5–5.5 | 1.5–4.5 | tactile + debounce |
| BTN_RIGHT | 34.5–38.5 | 1.5–4.5 | tactile + debounce |
| XIAO ESP32-C3 | 15.5–32.5 | 9–25.5 | castellated, 21 × 17.5 mm, antenna pointing right (+x) |

### 8.2 Bottom side (L4)

| Zone | x-range | y-range | Contents |
|---|---|---|---|
| Antenna keep-out | 0.5–17 | 22–32 | NO copper — including no L4 ground pour |
| BAT+ pad | 17–23 | 13–15 | flying-lead pad, 5 mm to BAT– |
| BAT– pad | 17–23 | 10.5–12.5 | flying-lead pad |
| LRA+ pad | 19.5–22.5 | 6–9 | extra copper for fatigue |
| LRA– pad | 24.5–27.5 | 6–9 | extra copper for fatigue |

L4 has GND fill everywhere except the antenna keep-out. Battery and LRA pads route to top side via through-vias.

### 8.3 Mechanical

- Mounting holes: M1.6 at (2, 30) and (38, 30) on the top corners
- Keyring hole: ⌀ 3 mm at (20, 30), 2 mm copper-clearance ring
- MEMS port: ⌀ 1 mm hole at center of ICS-43434 footprint (~ (10, 6)), through-PCB, aligns to enclosure opening
- Silkscreen on top side: every connector pin labeled; every test point labeled; "AURA v0.1 · 2026-05" near keyring hole
- Silkscreen on bottom side: BAT+, BAT–, LRA+, LRA– labels; reflow-side fiducials

## 9. Critical layout rules

| Tag | Rule |
|---|---|
| **RF** | Antenna keep-out 5 mm on **all four layers** — including L4 GND pour |
| **RF** | RFIN trace from antenna feed to AD8317 RFIN ≤ 5 mm, no vias |
| **RF** | AD8317 LFCSP exposed pad stitched to L2 with 4 × 0.3 mm vias |
| **RF** | Ground stitching vias every 3 mm along RF section perimeter |
| **PWR** | TPS62840 input loop < 5 mm²: C_IN → VIN → SW → L1 → VOUT → C_OUT, return via shortest path |
| **PWR** | 3V3_RF island on L3 joined to 3V3_D only at FB1 footprint, single-point |
| **PWR** | C_IN GND and C_OUT GND tied directly together at the IC pad |
| **EMC** | No high-current trace (BAT+, LRA drive, USB charging) crosses under AD8317 or LIS2MDL |
| **EMC** | LIS2MDL ≥ 8 mm from LRA, antenna, and battery protection FETs (XIAO has these) |
| **EMC** | I²C traces: 0.15 mm width, length-matched within 5 mm, NOT routed over LIS2MDL |
| **SIG** | AD8317 VOUT trace ≤ 30 mm to XIAO ADC, RC LPF (1 kΩ + 100 nF) at AD8317 side, route on inner layer if practical |
| **SIG** | SPI to e-paper: 0.15 mm, ground-referenced, no length-match required |
| **MECH** | 2 mm copper clearance ring around keyring hole and both M1.6 mounts |
| **MECH** | LRA pads with 0.5 mm extra copper for vibration fatigue |
| **MECH** | Microphone acoustic port ⌀ 1 mm aligned to enclosure opening |
| **DECOUP** | All decoupling caps within 2 mm of their IC's power pin (AD8317 VPOS within 1 mm) |
| **PROCESS** | All ICs in non-BGA packages (largest is LFCSP-8 + LGA-12); no QFN smaller than 3 mm |

## 10. Phase plan (revised under Toolchain Amendment)

| # | Phase | Deliverable | Gate |
|---|---|---|---|
| **P0** | Tooling setup | `uv`, `skidl`, KiCad 10 verified; project scaffold; custom symbol library `lib/aura.kicad_sym` with AD8317, TPS62840, XIAO_ESP32_C3 | env sanity check |
| **P1** | SKiDL schematic | All `elec/src/**/*.py` modules written, `ERC()` clean, `aura.net` (KiCad netlist) + `aura_bom.csv` + `aura_connectivity.md` generated | **Gate A**: connectivity-doc walk-through, BOM/MPN review |
| **P2** | KiCad bootstrap | KiCad project, JLC04161H-7628 stackup configured, DRC profile loaded, netlist imported, footprints verified, board outline + mech features placed | sanity check |
| **P3** | Placement | all components placed per §8 floorplan, LIS2MDL clearance verified, MEMS port aligned, XIAO antenna oriented | **Gate B**: 3D preview review against enclosure CAD |
| **P4** | Routing | RF chain first, then VOUT signal, I²C, SPI, I²S, power (tight buck loop), control. No vias in RF path. | sub-checkpoint after RF + power |
| **P5** | Pours + stitching | L2 GND uninterrupted under RF; L3 power islands w/ FB1 single-point join; L4 GND with antenna keep-out preserved; perimeter + RF stitching | **Gate C**: copper review, antenna keep-out audit on all 4 layers |
| **P6** | Outputs + verification | DRC = 0 errors, 3D STEP, Gerbers + Excellon drill, CPL, final BOM with alternates, design-notes.md (pin map, antenna tuning notes, populate order, known risks) | **Gate D**: pre-fab review — user submits to JLCPCB |

Practical setup notes (P0):
- `uv tool install skidl` installs SKiDL into a uv-managed venv
- `KICAD8_SYMBOL_DIR=/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols` env var lets SKiDL find KiCad 10's symbol libraries (KiCad 8 → 10 symbol format is forward-compatible)
- KiCad standard libraries cover: LIS2MDL (Sensor_Magnetic), TCA9534 (Interface_Expansion), DRV2605LDGS (Driver), ICS-43434 (Sensor_Audio), 2N7002 (Transistor_FET), Polyfuse + L + FerriteBead_Small (Device), Conn_01x24 (Connector_Generic), SW_Push (Switch)
- Custom symbols vendored in `lib/aura.kicad_sym`: **AD8317** (no KiCad standard symbol; only AD8313 is similar), **TPS62840** (KiCad standard has TPS62823/62836/62842 but not 62840), **XIAO_ESP32_C3** (modeled as 14-pin connector with named labels matching XIAO silkscreen — bare ESP32-C3-WROOM-02 from MCU_Espressif library is a different abstraction level)
- LFCSP-8 (AD8317) footprint: KiCad's `Package_DFN_QFN:LFCSP-8-1EP_3x2mm_P0.5mm_EP1.6x1.4mm` — verify against ADI datasheet section 11
- FH12-24S-0.5SH (FPC): KiCad's `Connector_FFC-FPC:Hirose_FH12-24S-0.5SH_1x24-1MP_P0.50mm_Horizontal` — verify pin 1 orientation against e-paper module datasheet

Effort estimate (W1, agent-driven through P1; mixed agent + KiCad GUI through P2–P6): P1 ~1 session of agent work (this Plan 1), P2 ~½ day, P3 ~1 day, P4 ~2–3 days (RF rules), P5 ~1 day, P6 ~½ day. **Realistic total from this point: 1 week of layout work** after Plan 1 lands.

## 11. Hand-assembly notes

- All ICs are in non-BGA packages (LFCSP, VSSOP, LGA up to 12 pins)
- Stencil-applied solder paste, hotplate or oven reflow
- Population order:
  1. **RF section first** (AD8317 + matching network) — RF-test before adding noise sources
  2. Power section (TPS62840 + L1 + Q1 gate) — verify 3V3_D rail
  3. Digital sensors (LIS2MDL, ICS-43434, TCA9534A)
  4. Haptic (DRV2605L)
  5. Connectors (FPC, button switches)
  6. **XIAO module last** (so it can be removed for rework)
  7. Battery and LRA flying-lead solder after all SMT verified

## 12. Known risks (verify on first power-up)

| Risk | Impact | Mitigation / verification |
|---|---|---|
| **WiFi/BLE coexistence with AD8317** | TX bursts saturate detector | Firmware time-multiplexes; sample AD8317 only with radio off |
| **Antenna S11 outside band** | Poor RF detection sensitivity | VNA S11 sweep on first build; populate DNP tuning stub if needed |
| **Buck/LDO contention if Q1 fails** | 3V3_D contention when USB-C plugged | Verify EN gating with USB plug/unplug + meter on EN net |
| **GPIO9 (SCK) strapping** | Boot fails if pulled low at reset | 10 kΩ pull-up to 3V3_D on SCK net; verify boot reliability |
| **DRV2605L ENABLE default low** | Haptic doesn't fire if expander not configured | Firmware drives P7 high before triggering effects |
| **TCA9534A address conflict** | Bus collision if A0–A2 not strapped | Verify I²C scan returns 0x1E, 0x38, 0x5A on first power-up |
| **MEMS port alignment** | Enclosure mismatch → muffled audio | Verify with calipers against enclosure CAD before fab |
| **LRA mounting fatigue** | Pads crack from vibration | 0.5 mm extra copper, VHB to enclosure, not solder-bridged to PCB body |
| **Polyswitch leakage in deep sleep** | Sleep current higher than budget | 0Ω jumper builds for sleep-Iq measurement; production builds have polyswitch only |
| **XIAO module Z-height** | Castellated stack may exceed 3 mm budget | Confirm with calipers before enclosure fab |

## 13. Verification checklist (pre-fab)

- [ ] Antenna keep-out is empty on **ALL four layers** including L4 ground pour
- [ ] AD8317 has continuous GND under it from L2
- [ ] No high-current trace (BAT+, LRA drive, USB charge) crosses under AD8317 or LIS2MDL
- [ ] 3V3_D and 3V3_RF connect at exactly one point (FB1 footprint)
- [ ] All decoupling caps placed within 2 mm of their IC's power pin (within 1 mm for AD8317 VPOS)
- [ ] DRC passes with zero errors
- [ ] ERC passes with zero errors
- [ ] LFCSP exposed pad dimensions match ADI datasheet
- [ ] FH12-24S-0.5SH pin 1 orientation matches e-paper datasheet
- [ ] PCB outline matches enclosure CAD (40 × 32 mm landscape, 3 mm corner radius, mounting holes aligned)
- [ ] Test points BAT+, 3V3_D, 3V3_RF, AD8317_VOUT, I2C_SDA, I2C_SCL, GND all present and labeled on silkscreen
- [ ] Strapping pull-ups on D8 (I2S_DIN) and D9 (SPI_SCK) populated
- [ ] DNP tuning stub footprint between antenna feed and shunt is present and unpopulated
- [ ] Q1 NFET inverter wired correctly: VBUS → gate, drain → buck EN, source → GND, 100 kΩ from drain to BAT+_FUSED

## 14. Project conventions

- **Reference designators:** U (ICs), R (resistors), C (caps), L (inductors), M (motors / LRA), J (connectors), BT (battery), SW (switches), ANT (antenna), FB (ferrite bead), F (fuse), TP (test point), Q (transistors)
- **Net names:** ALL_CAPS with underscores (`AD8317_VOUT_FILT`, `I2C_SDA`, `BAT_PLUS`)
- **Reference numbering:** by function block — U1 = MCU module, U2 = AD8317, U3 = DRV2605L, U4 = LIS2MDL, U5 = ICS-43434, U6 = TPS62840, U7 = TCA9534A
- **Hierarchical sheet names** (atopile leaf modules): `power/buck`, `power/battery`, `mcu/xiao_c3`, `rf/ad8317`, `rf/matching`, `rf/antenna`, `sensors/magnetometer`, `sensors/microphone`, `haptic/drv2605l`, `display/epaper_fpc`, `display/epaper_signals`, `io/buttons`, `io/battery_monitor`, `io/expander`
- **Single GND net** — no AGND/DGND symbolic split. Isolation is geometric (plane shape on L2, return-path routing).

## 15. Open items (deferred to implementation, not blockers for this spec)

- LRA part number selection (any 8 × 3 mm coin LRA at ~150 Hz–230 Hz resonance is acceptable; the DRV2605L will be configured to drive the chosen LRA)
- Alternate LCSC C-numbers per BOM line (filled in during P1)
- Final 200 mAh LiPo cell selection (any 3.7 V LiPo with ≤ 30 × 20 × 3 mm dimensions and JST-PH or flying-lead termination)
- Q1 alternate (2N7002 is the default; any equivalent N-ch logic-level FET in SOT-23 is acceptable)

These do not affect the locked architectural decisions and will be resolved during the schematic phase with no impact on layout.
