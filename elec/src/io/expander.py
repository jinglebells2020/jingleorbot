"""TCA9534A — 8-bit I²C GPIO expander @ 0x38.

Pinout (KiCad Interface_Expansion:TCA9534):
   1 A0      → GND  (address strap)
   2 A1      → GND
   3 A2      → GND
   4 P0      → BTN_LEFT
   5 P1      → BTN_RIGHT
   6 P2      → AD8317_ENBL
   7 P3      → EPD_RST
   8 GND
   9 P4      → EPD_CS
  10 P5      → EPD_BUSY
  11 P6      → LIS2MDL_DRDY
  12 P7      → DRV2605L_ENABLE
  13 ~{INT}  → EXP_INT (pulled up to V3V3_D via 10 kΩ)
  14 SCL
  15 SDA
  16 VDD

Decoupling: 100 nF at VDD within 1 mm.
"""
from skidl import Part


def build(nets: dict) -> None:
    u7 = Part("Interface_Expansion", "TCA9534",
              value="TCA9534APWR",
              footprint="Package_SO:TSSOP-16_4.4x5mm_P0.65mm",
              fields={"LCSC": "C103079", "MPN": "TCA9534APWR"})

    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric",
                 fields={"LCSC": "C1525"})

    r_int = Part("Device", "R", value="10k",
                 footprint="Resistor_SMD:R_0402_1005Metric",
                 fields={"LCSC": "C25744"})

    # SKiDL's string-key lookup matches name AND number, so e.g. u7["P1"] would
    # also match pin number 1 (A0). Use exact pin numbers for the port pins.
    # TCA9534 KiCad pinout:
    #   1 A0 | 2 A1 | 3 A2 | 4 P0 | 5 P1 | 6 P2 | 7 P3 | 8 GND
    #   9 P4 | 10 P5 | 11 P6 | 12 P7 | 13 ~{INT} | 14 SCL | 15 SDA | 16 VDD

    # Power
    nets["V3V3_D"] += u7[16]   # VDD
    nets["GND"]    += u7[8]    # GND

    # Address strap → 0x38
    nets["GND"] += u7[1]   # A0
    nets["GND"] += u7[2]   # A1
    nets["GND"] += u7[3]   # A2

    # I²C
    nets["I2C_SCL"] += u7[14]
    nets["I2C_SDA"] += u7[15]

    # INT (active-low) with pull-up
    nets["EXP_INT"] += u7[13]
    u7[13]          += r_int[1]
    nets["V3V3_D"]  += r_int[2]

    # Port allocation by pin number
    nets["BTN_LEFT"]        += u7[4]    # P0
    nets["BTN_RIGHT"]       += u7[5]    # P1
    nets["AD8317_ENBL"]     += u7[6]    # P2
    nets["EPD_RST"]         += u7[7]    # P3
    nets["EPD_CS"]          += u7[9]    # P4
    nets["EPD_BUSY"]        += u7[10]   # P5
    nets["LIS2MDL_DRDY"]    += u7[11]   # P6
    nets["DRV2605L_ENABLE"] += u7[12]   # P7

    # Decoupling
    nets["V3V3_D"] += c_dec[1]
    nets["GND"]    += c_dec[2]
