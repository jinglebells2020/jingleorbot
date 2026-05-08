"""ICS-43434 — I²S MEMS microphone, left channel.

Pinout (KiCad Sensor_Audio:ICS-43434):
  1 WS  → I2S_LRCLK
  2 LR  → GND (left channel)
  3 GND
  4 SCK → I2S_BCLK
  5 VDD
  6 SD  → I2S_DIN

Decoupling: 100 nF + 10 µF X5R per datasheet.
"""
from skidl import Part


def build(nets: dict) -> None:
    u5 = Part("Sensor_Audio", "ICS-43434",
              value="ICS-43434",
              footprint="Sensor_Audio:Knowles_LGA-6_3.5x2.65mm",
              fields={"LCSC": "C353473", "MPN": "ICS-43434"})

    c_hf = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric",
                fields={"LCSC": "C1525"})
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0603_1608Metric",
                  fields={"LCSC": "C19702"})

    nets["I2S_LRCLK"] += u5["WS"]
    nets["GND"]       += u5["LR"]      # left channel
    nets["GND"]       += u5["GND"]
    nets["I2S_BCLK"]  += u5["SCK"]
    nets["V3V3_D"]    += u5["VDD"]
    nets["I2S_DIN"]   += u5["SD"]

    nets["V3V3_D"] += c_hf[1]
    nets["GND"]    += c_hf[2]
    nets["V3V3_D"] += c_bulk[1]
    nets["GND"]    += c_bulk[2]
