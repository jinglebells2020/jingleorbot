"""LIS2MDLTR — 3-axis magnetometer @ I2C 0x1E.

Decoupling: 100 nF at Vdd, 100 nF at Vdd_IO, 100 nF C0G across the C1 pin
(internal regulator reservoir per datasheet).
~{CS} tied high for I²C mode select.  SA address strap is built into the
SDA/SDI/SDO pin behavior; default I²C address 0x1E with the chip's slave
address bit configured via firmware.
DRDY routes to TCA9534A P6 (LIS2MDL_DRDY net).
NC pins (2, 11, 12) left unconnected — KiCad will warn but not error.
"""
from skidl import Part
import builtins
NC = builtins.NC  # SKiDL injects NC into builtins on import; alias for clarity


def build(nets: dict) -> None:
    u4 = Part("Sensor_Magnetic", "LIS2MDL",
              value="LIS2MDLTR",
              footprint="Package_LGA:LGA-12_2x2mm_P0.5mm_LayoutBorder3x4y",
              fields={"LCSC": "C504428", "MPN": "LIS2MDLTR"})

    c_vdd   = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric",
                   fields={"LCSC": "C1525"})
    c_vddio = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric",
                   fields={"LCSC": "C1525"})
    c_res   = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric",
                   fields={"LCSC": "C307331", "Note": "C0G/NP0 reservoir on C1"})

    nets["I2C_SCL"]      += u4["SCL/SPC"]
    nets["I2C_SDA"]      += u4["SDA/SDI/SDO"]
    nets["LIS2MDL_DRDY"] += u4["DRDY"]
    nets["V3V3_D"]       += u4["~{CS}"]    # CS high → I²C mode
    nets["V3V3_D"]       += u4["Vdd"]
    nets["V3V3_D"]       += u4["Vdd_IO"]
    for p in u4.get_pins("GND"):
        nets["GND"] += p

    # Reservoir cap on C1
    u4["C1"]    += c_res[1]
    nets["GND"] += c_res[2]

    # Decoupling
    nets["V3V3_D"] += c_vdd[1]
    nets["GND"]    += c_vdd[2]
    nets["V3V3_D"] += c_vddio[1]
    nets["GND"]    += c_vddio[2]

    # NC pins — explicitly mark as no-connect to suppress ERC warnings
    for nc_pin in u4.get_pins("NC"):
        nc_pin += NC
