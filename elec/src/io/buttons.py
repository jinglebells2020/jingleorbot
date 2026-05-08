"""Two C&K PTS815 tactile switches with debounce + ESD series.

Wiring per button:
    GPIO --[1kΩ ESD]--+-- switch -- GND
                      |
                      +-- 100nF --- GND

GPIOs are driven into the TCA9534A P0 (BTN_LEFT) and P1 (BTN_RIGHT).
"""
from skidl import Part


def _one_button(nets: dict, gpio_net_name: str) -> None:
    sw = Part("Switch", "SW_Push",
              value="PTS815",
              footprint="Button_Switch_SMD:SW_SPST_PTS810",
              fields={"LCSC": "C720477", "MPN": "PTS815SJM"})
    r_esd = Part("Device", "R", value="1k",
                 footprint="Resistor_SMD:R_0402_1005Metric",
                 fields={"LCSC": "C11702"})
    c_deb = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric",
                 fields={"LCSC": "C1525"})

    nets[gpio_net_name] += r_esd[1]
    r_esd[2] += sw[1]
    nets["GND"] += sw[2]
    r_esd[2] += c_deb[1]
    nets["GND"] += c_deb[2]


def build(nets: dict) -> None:
    _one_button(nets, "BTN_LEFT")
    _one_button(nets, "BTN_RIGHT")
