"""TPS62840 buck regulator with !VBUS-gated EN and 2.2 µH inductor.

When USB-C is plugged in (VBUS_SENSE high), Q1 turns on and pulls the buck's
EN pin to GND, disabling the buck. The XIAO's onboard LDO drives 3V3_D in
that case. When USB-C is unplugged, Q1 turns off and R_EN_PULL pulls EN
high, enabling the buck from BAT+_FUSED.
"""
from skidl import Part, Net


def build(nets: dict) -> None:
    # TPS62840 buck (custom symbol in lib/aura.kicad_sym)
    u6 = Part("aura", "TPS62840",
              value="TPS62840DGRR",
              footprint="Package_SO:HVSSOP-8-1EP_3x3mm_P0.65mm_EP1.84x1.74mm",
              fields={"LCSC": "C2935262", "MPN": "TPS62840DGRR"})

    # Inductor — Murata DFE201610E-2R2M, 2.2 µH, 1 A sat
    l1 = Part("Device", "L",
              value="2.2uH",
              footprint="Inductor_SMD:L_Murata_DFE201610E_2.0x1.6mm",
              fields={"LCSC": "C232037", "MPN": "DFE201610E-2R2M"})

    # Input cap 10 µF X5R 0603
    c_in = Part("Device", "C",
                value="10uF",
                footprint="Capacitor_SMD:C_0603_1608Metric",
                fields={"LCSC": "C19702"})

    # Output caps: 10 µF + 100 nF in parallel
    c_out_bulk = Part("Device", "C",
                     value="10uF",
                     footprint="Capacitor_SMD:C_0603_1608Metric",
                     fields={"LCSC": "C19702"})
    c_out_hf = Part("Device", "C",
                    value="100nF",
                    footprint="Capacitor_SMD:C_0402_1005Metric",
                    fields={"LCSC": "C1525"})

    # !VBUS gate transistor — 2N7002 N-channel MOSFET
    q1 = Part("Transistor_FET", "2N7002",
              value="2N7002",
              footprint="Package_TO_SOT_SMD:SOT-23",
              fields={"LCSC": "C8545"})

    # 100 kΩ pull-up from EN to BAT+_FUSED
    r_en_pull = Part("Device", "R",
                     value="100k",
                     footprint="Resistor_SMD:R_0402_1005Metric",
                     fields={"LCSC": "C25741"})

    # 1 MΩ gate-source bleed for Q1 (turns it off cleanly when VBUS removed)
    r_q1_bleed = Part("Device", "R",
                      value="1M",
                      footprint="Resistor_SMD:R_0402_1005Metric",
                      fields={"LCSC": "C25898"})

    # Internal switch node — local net, not shared with others
    sw_node = Net("BUCK_SW")
    en_node = Net("BUCK_EN")

    # ----- TPS62840 main connections -----
    nets["BAT_PLUS_FUSED"] += u6["VIN"]
    nets["GND"] += u6["GND"]
    nets["GND"] += u6["EP"]            # exposed pad → GND
    en_node += u6["EN"]
    nets["GND"] += u6["MODE"]          # MODE = GND → power-save (PFM) mode
    sw_node += u6["SW"]
    u6["VOUT"] += u6["VOS"]            # internal feedback short for fixed-output 3.3 V variant
    u6["VOUT"] += u6["FB"]             # FB also tied if not using ext divider
    nets["V3V3_D"] += u6["VOUT"]

    # Input cap across VIN / GND
    nets["BAT_PLUS_FUSED"] += c_in[1]
    nets["GND"] += c_in[2]

    # Inductor between SW and VOUT
    sw_node += l1[1]
    nets["V3V3_D"] += l1[2]

    # Output caps across VOUT / GND
    nets["V3V3_D"] += c_out_bulk[1]
    nets["GND"] += c_out_bulk[2]
    nets["V3V3_D"] += c_out_hf[1]
    nets["GND"] += c_out_hf[2]

    # ----- Q1 inverter for !VBUS gating -----
    # 2N7002 pin order in KiCad: 1=Gate, 2=Source, 3=Drain
    nets["VBUS_SENSE"] += q1[1]        # gate ← VBUS_SENSE
    nets["GND"] += q1[2]               # source = GND
    en_node += q1[3]                   # drain → EN node

    # Q1 gate-source bleed (1 MΩ)
    nets["VBUS_SENSE"] += r_q1_bleed[1]
    nets["GND"] += r_q1_bleed[2]

    # EN pull-up to BAT+_FUSED (100 kΩ)
    en_node += r_en_pull[1]
    nets["BAT_PLUS_FUSED"] += r_en_pull[2]
