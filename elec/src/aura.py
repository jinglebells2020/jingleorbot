"""AURA EMF keychain — top-level SKiDL orchestrator.

Run: `uv run python elec/src/aura.py`

Outputs (under elec/build/):
    aura.net               — KiCad netlist for pcbnew import
    aura_bom.csv           — BOM with LCSC C-numbers
    aura_connectivity.md   — human-reviewable per-net endpoint catalog
    aura_erc.log           — ERC report (warnings + errors)
"""
from __future__ import annotations
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

# ----- SKiDL setup -----
ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = ROOT / "lib"
BUILD_DIR = ROOT / "elec" / "build"
KICAD_SYMBOL_DIR = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"

os.environ.setdefault("KICAD8_SYMBOL_DIR", KICAD_SYMBOL_DIR)
sys.path.insert(0, str(ROOT / "elec"))

import builtins
from skidl import (
    Part, Net, ERC, generate_netlist,
    lib_search_paths, KICAD8, set_default_tool,
    erc_logger,
)
import skidl as _skidl_top
set_default_tool(KICAD8)
lib_search_paths[KICAD8].append(str(LIB_DIR))


# Suppress "No footprint" errors for virtual parts (PWR_FLAG) — they're
# schematic-only annotations that don't go on the PCB. The standard test
# points get the TestPoint:TestPoint_Pad_D0.8mm footprint already.
def _quiet_empty_footprint(part) -> None:
    if part.name == "PWR_FLAG":
        return
    from skidl.logger import active_logger
    active_logger.bare_error(
        f"No footprint for {part.name}/{part.ref} added at {part.src_line(True)}."
    )

_skidl_top.empty_footprint_handler = _quiet_empty_footprint
# SKiDL injects `default_circuit` and `NC` into builtins on import — alias them locally
default_circuit = builtins.default_circuit
NC = builtins.NC

# ----- Imports of leaf modules (after SKiDL config) -----
from src.nets import make_nets
from src.power import battery, buck, rails
from src.mcu import xiao_c3
from src.rf import ad8317, matching
from src.sensors import magnetometer, microphone
from src.haptic import drv2605l
from src.display import epaper_fpc
from src.io import buttons, battery_monitor, expander


def add_power_flags(nets: dict) -> None:
    """KiCad PWR_FLAG markers tell ERC each named rail has a real power source.

    Without these, every rail looks like a 'pin with no driver' and emits
    'insufficient drive current' warnings. PWR_FLAG is a single-pin power-out
    placeholder that doesn't generate a footprint (excluded from BOM/PCB).
    """
    # V3V3_D is driven by TPS62840.VOUT (POWER-OUT); flagging it would create
    # a POWER-OUT vs POWER-OUT conflict. Same logic everywhere else: only
    # flag rails that have NO internal power-out source.
    flagged_rails = ["BAT_PLUS", "BAT_PLUS_FUSED", "V3V3_RF",
                     "VBUS_SENSE", "GND"]
    for rail in flagged_rails:
        # PWR_FLAG is a schematic-only annotation — give it an empty footprint
        # so the netlist generator doesn't error on it being placed on the PCB.
        flag = Part("power", "PWR_FLAG", value=rail, ref_prefix="PWR_FLAG",
                    footprint="")
        flag.do_erc = False        # ERC sees a single POWER-OUT, no driver — silence
        flag.dnp = True            # Do-not-populate; excluded from BOM/PCB
        nets[rail] += flag[1]


def add_mechanical(nets: dict) -> None:
    """Mechanical pads: battery pads (bottom side), LRA pads, test points."""
    # Battery solder pads (bottom side, BAT± flying-lead pads)
    bat_pads = Part("Connector_Generic", "Conn_01x02",
                    value="BAT_PADS",
                    footprint="Connector_Wire:SolderWire-0.5sqmm_1x02_P5.0mm_D0.9mm_OD2.1mm",
                    fields={"Note": "BAT+/BAT- 5mm pad spacing, bottom side"})
    nets["BAT_PLUS"]  += bat_pads["Pin_1"]
    nets["BAT_MINUS"] += bat_pads["Pin_2"]
    nets["GND"]       += bat_pads["Pin_2"]    # BAT- is GND

    # LRA solder pads (bottom side, 5 mm spacing, +0.5 mm extra copper for fatigue)
    lra_pads = Part("Connector_Generic", "Conn_01x02",
                    value="LRA_PADS",
                    footprint="Connector_Wire:SolderWire-0.5sqmm_1x02_P5.0mm_D0.9mm_OD2.1mm",
                    fields={"Note": "LRA motor pads, bottom side, 8x3 mm coin"})
    nets["LRA_PLUS"]  += lra_pads["Pin_1"]
    nets["LRA_MINUS"] += lra_pads["Pin_2"]

    # Test points — 7 round 0.8 mm pads
    tp_specs = [
        ("TP1", "BAT_PLUS"),
        ("TP2", "V3V3_D"),
        ("TP3", "V3V3_RF"),
        ("TP4", "AD8317_VOUT_FILT"),
        ("TP5", "I2C_SDA"),
        ("TP6", "I2C_SCL"),
        ("TP7", "GND"),
    ]
    for tp_name, net_key in tp_specs:
        tp = Part("Connector", "TestPoint",
                  ref_prefix="TP",
                  value=net_key,
                  footprint="TestPoint:TestPoint_Pad_D0.8mm")
        tp.ref = tp_name
        nets[net_key] += tp[1]


def write_connectivity_doc(out_path: Path) -> None:
    """Walk all nets and write a per-net endpoint catalog as Markdown."""
    nets_in_circuit = list(default_circuit.nets)
    nets_in_circuit.sort(key=lambda n: n.name)

    lines = [
        "# AURA — Connectivity Catalog\n",
        "Generated by `elec/src/aura.py`. One row per (net, endpoint).\n",
        "Endpoints listed as `<part-ref>.<pin-num> (<pin-name>)`.\n",
        f"Total nets: {len(nets_in_circuit)}\n",
        "\n## Net listing\n",
    ]
    for net in nets_in_circuit:
        if not net.pins:
            continue
        lines.append(f"\n### `{net.name}`  ({len(net.pins)} pin{'s' if len(net.pins) != 1 else ''})\n")
        rows = sorted([(p.part.ref or "?", p.num, p.name) for p in net.pins])
        for ref, num, name in rows:
            lines.append(f"- `{ref}.{num}` ({name})\n")

    out_path.write_text("".join(lines))


def write_bom(out_path: Path) -> None:
    """Write a BOM CSV grouping equivalent parts."""
    parts_in_circuit = list(default_circuit.parts)
    # Group by (value, footprint, lcsc) so same-spec parts merge
    groups: dict[tuple, list] = defaultdict(list)
    for p in parts_in_circuit:
        lcsc = (p.fields.get("LCSC") if hasattr(p, "fields") else "") or ""
        # Some parts (test points, generic connectors) don't carry LCSC
        key = (str(p.value or ""), str(p.footprint or ""), lcsc)
        groups[key].append(p)

    rows = []
    for (value, fp, lcsc), parts in sorted(groups.items()):
        refs = ", ".join(sorted(p.ref for p in parts if p.ref))
        rows.append({
            "Quantity": len(parts),
            "References": refs,
            "Value": value,
            "Footprint": fp,
            "LCSC": lcsc,
        })

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Quantity", "References", "Value", "Footprint", "LCSC"])
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Build the design
    nets = make_nets()
    battery.build(nets)
    buck.build(nets)
    rails.build(nets)
    xiao_c3.build(nets)
    ad8317.build(nets)
    matching.build(nets)
    magnetometer.build(nets)
    microphone.build(nets)
    drv2605l.build(nets)
    epaper_fpc.build(nets)
    buttons.build(nets)
    battery_monitor.build(nets)
    expander.build(nets)
    add_mechanical(nets)
    add_power_flags(nets)

    # 2. Run ERC
    erc_log = BUILD_DIR / "aura_erc.log"
    erc_logger.handlers.clear()
    import logging
    fh = logging.FileHandler(str(erc_log), mode="w")
    fh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    erc_logger.addHandler(fh)
    ERC()
    fh.close()

    # 3. Generate KiCad netlist
    netlist_path = BUILD_DIR / "aura.net"
    generate_netlist(file_=str(netlist_path))

    # 4. Generate BOM
    bom_path = BUILD_DIR / "aura_bom.csv"
    write_bom(bom_path)

    # 5. Generate connectivity doc
    conn_path = BUILD_DIR / "aura_connectivity.md"
    write_connectivity_doc(conn_path)

    # 6. Generate KiCad schematic file (.kicad_sch) — fast grid layout via
    # custom emitter (SKiDL's force-directed default times out on this design).
    from src.gen_schematic import write_kicad_sch, write_lib_tables
    sch_path = BUILD_DIR / "aura.kicad_sch"
    try:
        write_kicad_sch(sch_path)
        write_lib_tables(BUILD_DIR)
    except Exception as e:
        print(f"  schematic gen FAILED: {type(e).__name__}: {e}")
        sch_path = None

    # Summary
    parts = list(default_circuit.parts)
    nets_used = [n for n in default_circuit.nets if n.pins]
    print(f"AURA build complete:")
    print(f"  parts:    {len(parts)}")
    print(f"  nets:     {len(nets_used)}")
    print(f"  netlist:    {netlist_path.relative_to(ROOT)}")
    print(f"  BOM:        {bom_path.relative_to(ROOT)}")
    print(f"  conn doc:   {conn_path.relative_to(ROOT)}")
    print(f"  ERC log:    {erc_log.relative_to(ROOT)}")
    if sch_path and sch_path.exists():
        print(f"  schematic:  {sch_path.relative_to(ROOT)}")
    else:
        print(f"  schematic:  NOT GENERATED (see error above)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
