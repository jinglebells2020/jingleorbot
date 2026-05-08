"""Battery + polyswitch + 0Ω bypass jumper.

BAT+ flows through F1 (1A polyswitch, MF-FSMF110X) before reaching the buck.
A 0Ω jumper in parallel allows bypassing F1 for sleep-Iq measurement.
"""
from skidl import Part


def build(nets: dict) -> None:
    f1 = Part("Device", "Polyfuse",
              value="MF-FSMF110X 1A",
              footprint="Fuse:Fuse_1206_3216Metric",
              fields={"LCSC": "C914043"})

    bypass = Part("Device", "R",
                  value="0R",
                  footprint="Resistor_SMD:R_0805_2012Metric",
                  fields={"LCSC": "C17168", "Note": "DNP in production; populate alone for sleep-Iq"})

    # Polyswitch from BAT+ → BAT+_FUSED
    nets["BAT_PLUS"]       += f1[1]
    nets["BAT_PLUS_FUSED"] += f1[2]

    # 0Ω bypass jumper in parallel
    nets["BAT_PLUS"]       += bypass[1]
    nets["BAT_PLUS_FUSED"] += bypass[2]
