"""24-pin Hirose FH12-24S-0.5SH FPC connector + e-paper boost circuit caps.

Pinout per Good Display GDEW0102I4FC class panels:
   1 VDD          → V3V3_D
   2 GND
   3 BS           → GND (4-line SPI mode)
   4 BUSY         → EPD_BUSY (TCA9534A P5)
   5 RES          → EPD_RST (TCA9534A P3)
   6 D/C          → EPD_DC (XIAO D0)
   7 CS           → EPD_CS (TCA9534A P4)
   8 SCL (SPI)    → SPI_SCK
   9 SDA (SPI)    → SPI_MOSI
  10–11 NC
  12 VCOM         → NC (some panels need cap to VDD; verify against module)
  13 VSL          → 1 µF cap to GND
  14 VSH          → 1 µF cap to GND
  15 VPP          → V3V3_D (program voltage)
  16 VGL          → 1 µF cap to GND
  17 VGH          → 1 µF cap to GND
  18–21 NC
  22–24 GND
"""
from skidl import Part
import builtins
NC = builtins.NC


def build(nets: dict) -> None:
    j1 = Part("Connector_Generic", "Conn_01x24",
              value="FH12-24S-0.5SH",
              footprint="Connector_FFC-FPC:Hirose_FH12-24S-0.5SH_1x24-1MP_P0.50mm_Horizontal",
              fields={"LCSC": "C90105", "MPN": "FH12-24S-0.5SH"})

    # Boost circuit caps (4 × 1 µF X5R 0402, within 5 mm of FPC per spec)
    boost_caps = []
    for which in ("VSL", "VSH", "VGL", "VGH"):
        c = Part("Device", "C", value="1uF",
                 footprint="Capacitor_SMD:C_0402_1005Metric",
                 fields={"LCSC": "C52923", "Note": f"e-paper boost cap {which}"})
        boost_caps.append(c)
    c_vsl, c_vsh, c_vgl, c_vgh = boost_caps

    # Map FPC pins (KiCad symbol uses Pin_1, Pin_2, ... names)
    nets["V3V3_D"]   += j1["Pin_1"]    # VDD
    nets["GND"]      += j1["Pin_2"]
    nets["GND"]      += j1["Pin_3"]    # BS = GND for 4-line SPI
    nets["EPD_BUSY"] += j1["Pin_4"]
    nets["EPD_RST"]  += j1["Pin_5"]
    nets["EPD_DC"]   += j1["Pin_6"]
    nets["EPD_CS"]   += j1["Pin_7"]
    nets["SPI_SCK"]  += j1["Pin_8"]
    nets["SPI_MOSI"] += j1["Pin_9"]
    j1["Pin_10"]     += NC
    j1["Pin_11"]     += NC
    j1["Pin_12"]     += NC          # VCOM — verify against e-paper module
    nets["V3V3_D"]   += j1["Pin_15"] # VPP
    j1["Pin_18"]     += NC
    j1["Pin_19"]     += NC
    j1["Pin_20"]     += NC
    j1["Pin_21"]     += NC
    nets["GND"]      += j1["Pin_22"]
    nets["GND"]      += j1["Pin_23"]
    nets["GND"]      += j1["Pin_24"]

    # Boost cap connections — boost net rails to GND on each cap
    j1["Pin_13"] += c_vsl[1]
    nets["GND"]  += c_vsl[2]
    j1["Pin_14"] += c_vsh[1]
    nets["GND"]  += c_vsh[2]
    j1["Pin_16"] += c_vgl[1]
    nets["GND"]  += c_vgl[2]
    j1["Pin_17"] += c_vgh[1]
    nets["GND"]  += c_vgh[2]
