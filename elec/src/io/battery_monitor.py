"""Battery voltage divider for XIAO ADC.

BAT+_FUSED --[200kΩ]--+-- BAT_SENSE (to XIAO D1 / GPIO3 / ADC1_3)
                      |
                      +-- 100nF -- GND
                      |
                      +--[200kΩ]-- GND

Divider ratio = 0.5 → 4.2 V max input maps to 2.1 V at the ADC, well within
the ESP32-C3 ADC1 range (0–3.1 V at default attenuation).
Total quiescent draw: 4.2 V / 400 kΩ ≈ 10.5 µA — acceptable for the sleep budget.
"""
from skidl import Part


def build(nets: dict) -> None:
    r_top = Part("Device", "R", value="200k",
                 footprint="Resistor_SMD:R_0402_1005Metric",
                 fields={"LCSC": "C25745"})
    r_bot = Part("Device", "R", value="200k",
                 footprint="Resistor_SMD:R_0402_1005Metric",
                 fields={"LCSC": "C25745"})
    c_filt = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric",
                  fields={"LCSC": "C1525"})

    nets["BAT_PLUS_FUSED"] += r_top[1]
    nets["BAT_SENSE"]      += r_top[2]
    nets["BAT_SENSE"]      += r_bot[1]
    nets["GND"]            += r_bot[2]

    nets["BAT_SENSE"] += c_filt[1]
    nets["GND"]       += c_filt[2]
