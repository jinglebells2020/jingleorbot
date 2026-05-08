"""Antenna → AD8317 input matching network.

Topology (ADI datasheet figure 38):
    ANT --[CIN 1nF]-- AD8317_RFIN
        |
        +--[52.3Ω shunt]-- GND
        |
    DNP tune stub footprint (RFIN_FROM_ANT to GND, populate post-VNA if S11 poor)

The antenna trace itself (the meander geometry) is drawn in pcbnew during
layout; this module just provides the named feed-point net (RFIN_FROM_ANT)
and the matching components.
"""
from skidl import Part


def build(nets: dict) -> None:
    # AC-couple cap CIN: 1 nF X7R 0402
    c_in = Part("Device", "C",
                value="1nF",
                footprint="Capacitor_SMD:C_0402_1005Metric",
                fields={"LCSC": "C1588"})

    # Shunt termination 52.3 Ω 1% 0402, on antenna side of CIN
    r_term = Part("Device", "R",
                  value="52.3R",
                  footprint="Resistor_SMD:R_0402_1005Metric",
                  fields={"LCSC": "C12779", "Note": "1% precision termination"})

    # DNP tune stub footprint (0Ω 0402; populate as cap if VNA S11 poor)
    r_tune = Part("Device", "R",
                  value="DNP",
                  footprint="Resistor_SMD:R_0402_1005Metric",
                  fields={"LCSC": "C17168", "Note": "DNP at fab; tune post-VNA"})

    # CIN: antenna feed → AC-couple → AD8317 RFIN
    nets["RFIN_FROM_ANT"] += c_in[1]
    nets["AD8317_RFIN"]   += c_in[2]

    # Shunt termination on antenna side (between RFIN_FROM_ANT and GND)
    nets["RFIN_FROM_ANT"] += r_term[1]
    nets["GND"]           += r_term[2]

    # DNP tune stub between AD8317-side and GND
    nets["AD8317_RFIN"] += r_tune[1]
    nets["GND"]         += r_tune[2]
