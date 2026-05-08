"""DRV2605LDGS — haptic motor driver @ I²C 0x5A.

Pinout (KiCad Driver:DRV2605LDGS):
  1 REG       → 1 µF cap to GND (internal regulator output)
  2 SCL       → I2C_SCL
  3 SDA       → I2C_SDA
  4 IN/TRIG   → no-connect (I²C-only mode)
  5 EN        → DRV2605L_ENABLE (TCA9534A P7)
  6 VDD/NC    → no-connect (this is the alt VDD position; main VDD is pin 10)
  7 OUT+      → LRA_PLUS
  8 GND
  9 OUT-      → LRA_MINUS
  10 VDD      → V3V3_D

Decoupling: 1 µF + 100 nF on VDD pin 10, plus the 1 µF REG cap.
"""
from skidl import Part
import builtins
NC = builtins.NC


def build(nets: dict) -> None:
    u3 = Part("Driver", "DRV2605LDGS",
              value="DRV2605LDGSR",
              footprint="Package_SO:VSSOP-10_3x3mm_P0.5mm",
              fields={"LCSC": "C92482", "MPN": "DRV2605LDGSR"})

    c_vdd_bulk = Part("Device", "C", value="1uF",
                      footprint="Capacitor_SMD:C_0402_1005Metric",
                      fields={"LCSC": "C52923"})
    c_vdd_hf   = Part("Device", "C", value="100nF",
                      footprint="Capacitor_SMD:C_0402_1005Metric",
                      fields={"LCSC": "C1525"})
    c_reg      = Part("Device", "C", value="1uF",
                      footprint="Capacitor_SMD:C_0402_1005Metric",
                      fields={"LCSC": "C52923"})

    nets["V3V3_D"]          += u3["VDD"]
    nets["GND"]             += u3["GND"]
    nets["I2C_SCL"]         += u3["SCL"]
    nets["I2C_SDA"]         += u3["SDA"]
    nets["DRV2605L_ENABLE"] += u3["EN"]
    nets["LRA_PLUS"]        += u3["OUT+"]
    nets["LRA_MINUS"]       += u3["OUT-"]

    # No-connect pins
    u3["IN/TRIG"] += NC
    u3["VDD/NC"]  += NC

    # REG cap (internal regulator)
    u3["REG"]   += c_reg[1]
    nets["GND"] += c_reg[2]

    # VDD decoupling
    nets["V3V3_D"] += c_vdd_bulk[1]
    nets["GND"]    += c_vdd_bulk[2]
    nets["V3V3_D"] += c_vdd_hf[1]
    nets["GND"]    += c_vdd_hf[2]
