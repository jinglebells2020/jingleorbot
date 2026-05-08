"""XIAO ESP32-C3 module + strap pull-ups for D8 (I2S_DIN) and D9 (SPI_SCK).

The locked pin map from the spec:
    D0 (GPIO2)  → EPD_DC               (strap pin: idle high OK)
    D1 (GPIO3)  → BAT_SENSE            (ADC input)
    D2 (GPIO4)  → AD8317_VOUT_FILT     (ADC input)
    D3 (GPIO5)  → EXP_INT              (interrupt input from TCA9534A)
    D4 (GPIO6)  → I2C_SDA
    D5 (GPIO7)  → I2C_SCL
    D6 (GPIO21) → I2S_BCLK             (repurposed from U0TXD)
    D7 (GPIO20) → I2S_LRCLK            (repurposed from U0RXD)
    D8 (GPIO8)  → I2S_DIN              (strap: 10 kΩ pull-up to V3V3_D)
    D9 (GPIO9)  → SPI_SCK              (strap: 10 kΩ pull-up to V3V3_D)
    D10 (GPIO10) → SPI_MOSI
    3V3 → V3V3_D
    5V/VBUS → VBUS_SENSE (high-Z)
    GND → GND (4 vias to L2 plane in layout)
"""
from skidl import Part


def build(nets: dict) -> None:
    u1 = Part("aura", "XIAO_ESP32_C3",
              value="XIAO ESP32-C3",
              footprint="Module:Seeed_XIAO_ESP32_C3",
              fields={"LCSC": "C2934897", "MPN": "XIAO ESP32-C3", "Note": "Seeed SKU 102991060"})

    # Pin-to-net map per the locked spec
    nets["EPD_DC"]            += u1["D0"]
    nets["BAT_SENSE"]         += u1["D1"]
    nets["AD8317_VOUT_FILT"]  += u1["D2"]
    nets["EXP_INT"]           += u1["D3"]
    nets["I2C_SDA"]           += u1["D4"]
    nets["I2C_SCL"]           += u1["D5"]
    nets["I2S_BCLK"]          += u1["D6"]
    nets["I2S_LRCLK"]         += u1["D7"]
    nets["I2S_DIN"]           += u1["D8"]
    nets["SPI_SCK"]           += u1["D9"]
    nets["SPI_MOSI"]          += u1["D10"]

    # Power
    nets["V3V3_D"]     += u1["3V3"]
    nets["VBUS_SENSE"] += u1["5V_VBUS"]
    # XIAO has 2 GND pads (pins 7 and 9 on the symbol); both to GND
    for gnd_pin in u1.get_pins("GND"):
        nets["GND"] += gnd_pin

    # Strap pull-ups: D8 and D9 must be high during boot
    r_d8_strap = Part("Device", "R",
                      value="10k",
                      footprint="Resistor_SMD:R_0402_1005Metric",
                      fields={"LCSC": "C25744", "Note": "GPIO8 strap pull-up"})
    r_d9_strap = Part("Device", "R",
                      value="10k",
                      footprint="Resistor_SMD:R_0402_1005Metric",
                      fields={"LCSC": "C25744", "Note": "GPIO9 strap pull-up"})

    nets["I2S_DIN"] += r_d8_strap[1]
    nets["V3V3_D"]  += r_d8_strap[2]
    nets["SPI_SCK"] += r_d9_strap[1]
    nets["V3V3_D"]  += r_d9_strap[2]

    # I2C bus pull-ups (4.7 kΩ to V3V3_D, near MCU)
    r_pu_sda = Part("Device", "R",
                    value="4.7k",
                    footprint="Resistor_SMD:R_0402_1005Metric",
                    fields={"LCSC": "C25804"})
    r_pu_scl = Part("Device", "R",
                    value="4.7k",
                    footprint="Resistor_SMD:R_0402_1005Metric",
                    fields={"LCSC": "C25804"})
    nets["I2C_SDA"] += r_pu_sda[1]
    nets["V3V3_D"]  += r_pu_sda[2]
    nets["I2C_SCL"] += r_pu_scl[1]
    nets["V3V3_D"]  += r_pu_scl[2]
