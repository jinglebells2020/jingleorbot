"""AD8317 RF log detector with full decoupling and output LPF.

Per ADI datasheet figure 38 (single-ended detector reference):
- VPOS decoupled with 100 nF ‖ 10 nF ‖ 1 nF X7R, all 0402, within 1 mm
- INHI tied to RFIN externally (single-ended drive)
- INLO AC-coupled to GND via 1 nF
- CLPF: 1 nF for max output bandwidth
- VSET tied to VOUT (detector mode)
- VOUT routed through 1 kΩ + 100 nF RC LPF to AD8317_VOUT_FILT
"""
from skidl import Part, Net


def build(nets: dict) -> None:
    u2 = Part("aura", "AD8317",
              value="AD8317ACPZ-R7",
              footprint="Package_DFN_QFN:LFCSP-8-1EP_3x2mm_P0.5mm_EP1.6x1.4mm",
              fields={"LCSC": "C485486", "MPN": "AD8317ACPZ-R7"})

    # Decoupling stack on VPOS (100n + 10n + 1n)
    c_vpos_100n = Part("Device", "C",
                       value="100nF",
                       footprint="Capacitor_SMD:C_0402_1005Metric",
                       fields={"LCSC": "C1525"})
    c_vpos_10n = Part("Device", "C",
                      value="10nF",
                      footprint="Capacitor_SMD:C_0402_1005Metric",
                      fields={"LCSC": "C1546"})
    c_vpos_1n = Part("Device", "C",
                     value="1nF",
                     footprint="Capacitor_SMD:C_0402_1005Metric",
                     fields={"LCSC": "C1588"})

    # CLPF + INLO caps
    c_clpf = Part("Device", "C",
                  value="1nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric",
                  fields={"LCSC": "C1588"})
    c_inlo = Part("Device", "C",
                  value="1nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric",
                  fields={"LCSC": "C1588"})

    # Output RC LPF: 1 kΩ + 100 nF
    r_lpf = Part("Device", "R",
                 value="1k",
                 footprint="Resistor_SMD:R_0402_1005Metric",
                 fields={"LCSC": "C11702"})
    c_lpf = Part("Device", "C",
                 value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric",
                 fields={"LCSC": "C1525"})

    # ----- AD8317 connections -----
    nets["V3V3_RF"]      += u2["VPOS"]
    nets["GND"]          += u2["EP"]
    nets["AD8317_ENBL"]  += u2["ENBL"]
    nets["AD8317_RFIN"]  += u2["RFIN"]
    nets["AD8317_RFIN"]  += u2["INHI"]   # INHI tied to RFIN per datasheet

    # INLO AC-coupled to GND
    u2["INLO"] += c_inlo[1]
    nets["GND"] += c_inlo[2]

    # CLPF cap to GND
    u2["CLPF"] += c_clpf[1]
    nets["GND"] += c_clpf[2]

    # VSET tied to VOUT (detector mode)
    raw_vout = Net("AD8317_VOUT_RAW")
    raw_vout += u2["VOUT"]
    raw_vout += u2["VSET"]

    # Output RC LPF: 1 kΩ in series, 100 nF to GND on filtered side
    raw_vout += r_lpf[1]
    nets["AD8317_VOUT_FILT"] += r_lpf[2]
    nets["AD8317_VOUT_FILT"] += c_lpf[1]
    nets["GND"] += c_lpf[2]

    # VPOS decoupling stack — all to GND
    for cap in (c_vpos_100n, c_vpos_10n, c_vpos_1n):
        nets["V3V3_RF"] += cap[1]
        nets["GND"]     += cap[2]
