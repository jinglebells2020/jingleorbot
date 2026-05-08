"""3V3_D ↔ FB1 ferrite ↔ 3V3_RF single-point split.

The ferrite bead BLM18PG471SN1D (470 Ω @ 100 MHz, 1 A) is the only path between
the digital rail and the RF rail. Layer-3 power-plane islands mirror this geometry.
"""
from skidl import Part


def build(nets: dict) -> None:
    fb1 = Part("Device", "FerriteBead_Small",
               value="BLM18PG471SN1D",
               footprint="Inductor_SMD:L_0603_1608Metric",
               fields={"LCSC": "C159334", "Note": "470Ω @ 100MHz, 1A"})

    nets["V3V3_D"]  += fb1[1]
    nets["V3V3_RF"] += fb1[2]
