"""Generate a viewable .kicad_sch from the SKiDL circuit.

SKiDL's built-in `generate_schematic()` uses a force-directed layout that's
prohibitively slow on this design (>15 min on 69 parts with no result).
This module emits a .kicad_sch directly:

  - All parts laid out in a regular grid (deterministic, fast)
  - Used symbols embedded in (lib_symbols ...)  so the file is portable
  - Each pin gets a global_label with its net name placed at the pin's
    actual coordinate, so KiCad's connectivity engine binds them via
    label-overlap (no wire routing needed)

Usage: imported by aura.py after generate_netlist(), called as
       write_kicad_sch(BUILD_DIR / "aura.kicad_sch")

Result is a valid KiCad 9 / 10 schematic file (version 20240901).
"""
from __future__ import annotations
import builtins
import re
import uuid as _uuid
from pathlib import Path

KICAD_SYMBOL_DIR = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")
_LIB_DIR_LOCAL = Path(__file__).resolve().parents[2] / "lib"

# Grid spacing (KiCad mm) — multiples of 1.27 mm so pins snap cleanly
GRID_X = 60.96   # 48 × 1.27
GRID_Y = 81.28   # 64 × 1.27
COLS = 8
PAGE_OFFSET = (20.32, 20.32)  # 16 × 1.27 each


def _snap(value: float, step: float = 1.27) -> float:
    """Round to the nearest multiple of step (default KiCad 1.27 mm grid)."""
    return round(value / step) * step


def _uuid4() -> str:
    return str(_uuid.uuid4())


def _extract_symbol(library: str, name: str) -> str:
    """Return the (symbol "<name>" ...) S-expression block from the library file.

    Looks first in lib/aura.kicad_sym, then in KiCad's standard symbol dir.
    """
    candidates = [
        _LIB_DIR_LOCAL / f"{library}.kicad_sym",
        KICAD_SYMBOL_DIR / f"{library}.kicad_sym",
    ]
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text()
        # Find (symbol "<name>" ... matching parens
        marker = f'(symbol "{name}"'
        idx = text.find(marker)
        if idx == -1:
            continue
        # Walk forward, counting parens, to find the matching close
        depth = 0
        i = idx
        while i < len(text):
            c = text[i]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return text[idx:i + 1]
            i += 1
    raise FileNotFoundError(f"Symbol {library}:{name} not found in {candidates}")


def _format_symbol_instance(part, x: float, y: float, top_uuid: str) -> str:
    """Emit (symbol ...) block for one part placed at (x, y)."""
    # SKiDL's part.lib is a SchLib; its filename is the library name (e.g., 'Device')
    lib_name = part.lib.filename if hasattr(part.lib, 'filename') else 'Unknown'
    sym_name = part.name
    inst_uuid = _uuid4()
    pin_lines = []
    for p in part.pins:
        pin_lines.append(f'\t\t(pin "{p.num}" (uuid "{_uuid4()}"))')
    pin_block = "\n".join(pin_lines)

    fp = (part.footprint or "").replace('"', '\\"')
    val = (str(part.value) or "").replace('"', '\\"')
    ref = part.ref or "?"

    return f"""\t(symbol
\t\t(lib_id "{lib_name}:{sym_name}")
\t\t(at {x:.2f} {y:.2f} 0)
\t\t(unit 1)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(dnp no)
\t\t(uuid "{inst_uuid}")
\t\t(property "Reference" "{ref}"
\t\t\t(at {x + 12:.2f} {y - 6:.2f} 0)
\t\t\t(effects (font (size 1.27 1.27)) (justify left))
\t\t)
\t\t(property "Value" "{val}"
\t\t\t(at {x + 12:.2f} {y - 3:.2f} 0)
\t\t\t(effects (font (size 1.27 1.27)) (justify left))
\t\t)
\t\t(property "Footprint" "{fp}"
\t\t\t(at {x:.2f} {y:.2f} 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(property "Datasheet" ""
\t\t\t(at {x:.2f} {y:.2f} 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
{pin_block}
\t\t(instances
\t\t\t(project "aura"
\t\t\t\t(path "/{top_uuid}"
\t\t\t\t\t(reference "{ref}") (unit 1))
\t\t\t)
\t\t)
\t)"""


def _format_global_label(net_name: str, x: float, y: float, orientation: int = 0) -> str:
    """Emit (global_label ...) at the given coordinate."""
    # KiCad rotation: 0=right, 90=up, 180=left, 270=down
    return f"""\t(global_label "{net_name}"
\t\t(shape input)
\t\t(at {x:.2f} {y:.2f} {orientation})
\t\t(effects (font (size 1.27 1.27)) (justify left))
\t\t(uuid "{_uuid4()}")
\t)"""


def _pin_global_position(part, pin, part_x: float, part_y: float):
    """Compute a pin's absolute (x, y) given the part placement.

    SKiDL's pin.x / pin.y are in mm relative to the symbol origin. y is
    inverted between SKiDL's coordinate system (positive up) and KiCad's
    schematic system (positive down), so we negate the y component.
    Snapped to KiCad's 1.27 mm grid.
    """
    px = part_x + (pin.x if pin.x is not None else 0)
    py = part_y - (pin.y if pin.y is not None else 0)
    return _snap(px), _snap(py)


def _grid_position(idx: int) -> tuple[float, float]:
    row, col = divmod(idx, COLS)
    return PAGE_OFFSET[0] + col * GRID_X, PAGE_OFFSET[1] + row * GRID_Y


def write_lib_tables(build_dir: Path) -> None:
    """Emit sym-lib-table + fp-lib-table for the project."""
    sym_table = """(sym_lib_table
\t(version 7)
\t(lib (name "aura")(type "KiCad")(uri "${KIPRJMOD}/../../lib/aura.kicad_sym")(options "")(descr "AURA project-local: AD8317, TPS62840, XIAO_ESP32_C3"))
)
"""
    fp_table = """(fp_lib_table
\t(version 7)
)
"""
    (build_dir / "sym-lib-table").write_text(sym_table)
    (build_dir / "fp-lib-table").write_text(fp_table)


def write_kicad_sch(out_path: Path) -> None:
    circuit = builtins.default_circuit
    parts = sorted(circuit.parts, key=lambda p: (p.ref_prefix, int(re.sub(r'\D', '', p.ref) or 0)))

    # Collect unique (library, symbol-name) pairs and embed their definitions.
    seen_syms: set[tuple[str, str]] = set()
    lib_symbol_blocks: list[str] = []
    for part in parts:
        lib_name = part.lib.filename if hasattr(part.lib, 'filename') else 'Unknown'
        sym_name = part.name
        key = (lib_name, sym_name)
        if key in seen_syms:
            continue
        seen_syms.add(key)
        try:
            block = _extract_symbol(lib_name, sym_name)
            # Re-key the symbol with the library prefix that KiCad expects
            block = block.replace(f'(symbol "{sym_name}"', f'(symbol "{lib_name}:{sym_name}"', 1)
            lib_symbol_blocks.append("\t\t" + block.replace("\n", "\n\t\t"))
        except FileNotFoundError as exc:
            print(f"  warning: {exc}; skipping symbol embed for {lib_name}:{sym_name}")

    # Build symbol instances + global labels
    top_uuid = _uuid4()
    symbol_instance_blocks: list[str] = []
    label_blocks: list[str] = []
    seen_label_positions: set[tuple[str, int, int]] = set()  # dedup labels at identical points

    no_connect_blocks: list[str] = []

    for idx, part in enumerate(parts):
        x, y = _grid_position(idx)
        symbol_instance_blocks.append(_format_symbol_instance(part, x, y, top_uuid))
        for pin in part.pins:
            net = None
            try:
                nets = pin.get_nets()
                net = nets[0] if nets else None
            except Exception:
                net = None
            px, py = _pin_global_position(part, pin, x, y)
            if net is None or net.name in ("__NOCONNECT", ""):
                # Emit a no-connect marker so eeschema's ERC stays quiet
                no_connect_blocks.append(
                    f'\t(no_connect (at {px:.2f} {py:.2f}) (uuid "{_uuid4()}"))'
                )
                continue
            # Round to 0.5 mm to dedup
            key = (net.name, round(px * 2) / 2, round(py * 2) / 2)
            if key in seen_label_positions:
                continue
            seen_label_positions.add(key)
            label_blocks.append(_format_global_label(net.name, px, py))

    # Assemble
    parts_block = "\n".join(symbol_instance_blocks)
    labels_block = "\n".join(label_blocks)
    libsyms_block = "\n".join(lib_symbol_blocks)
    nc_block = "\n".join(no_connect_blocks)

    out = f"""(kicad_sch
\t(version 20240901)
\t(generator "aurapcb_skidl_gen")
\t(generator_version "10.0")
\t(uuid "{top_uuid}")
\t(paper "A2")
\t(lib_symbols
{libsyms_block}
\t)
{parts_block}
{labels_block}
{nc_block}
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
)
"""
    out_path.write_text(out)
    print(f"  symbols embedded: {len(seen_syms)}")
    print(f"  symbol instances: {len(parts)}")
    print(f"  global labels:    {len(label_blocks)}")
    print(f"  no-connects:      {len(no_connect_blocks)}")
