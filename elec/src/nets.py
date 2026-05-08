"""Global named nets for the AURA EMF keychain design.

Single source of truth for the cross-cutting nets that flow between modules.
Per-module local nets stay inside their respective .py files.
"""
from skidl import Net


def make_nets() -> dict[str, Net]:
    """Return a dict of the named nets used across all leaf modules."""
    return {
        # Power rails
        "BAT_PLUS":         Net("BAT_PLUS"),         # raw LiPo, 3.0–4.2 V
        "BAT_MINUS":        Net("BAT_MINUS"),        # battery negative
        "BAT_PLUS_FUSED":   Net("BAT_PLUS_FUSED"),   # post-polyswitch
        "VBUS_SENSE":       Net("VBUS_SENSE"),       # 5 V from XIAO USB-C, sense only
        "V3V3_D":           Net("V3V3_D"),           # buck output, digital + sensors
        "V3V3_RF":          Net("V3V3_RF"),          # ferrite-isolated, AD8317 only
        "GND":              Net("GND"),              # single ground net
        # Buses
        "I2C_SDA":          Net("I2C_SDA"),
        "I2C_SCL":          Net("I2C_SCL"),
        "SPI_SCK":          Net("SPI_SCK"),
        "SPI_MOSI":         Net("SPI_MOSI"),
        "I2S_BCLK":         Net("I2S_BCLK"),
        "I2S_LRCLK":        Net("I2S_LRCLK"),
        "I2S_DIN":          Net("I2S_DIN"),
        # MCU direct GPIOs
        "EPD_DC":           Net("EPD_DC"),
        "BAT_SENSE":        Net("BAT_SENSE"),
        "AD8317_VOUT_FILT": Net("AD8317_VOUT_FILT"),
        "EXP_INT":          Net("EXP_INT"),
        # Expander port outputs
        "BTN_LEFT":         Net("BTN_LEFT"),
        "BTN_RIGHT":        Net("BTN_RIGHT"),
        "AD8317_ENBL":      Net("AD8317_ENBL"),
        "EPD_RST":          Net("EPD_RST"),
        "EPD_CS":           Net("EPD_CS"),
        "EPD_BUSY":         Net("EPD_BUSY"),
        "LIS2MDL_DRDY":     Net("LIS2MDL_DRDY"),
        "DRV2605L_ENABLE":  Net("DRV2605L_ENABLE"),
        # Local-but-named for readability
        "RFIN_FROM_ANT":    Net("RFIN_FROM_ANT"),    # antenna feed → matching network
        "AD8317_RFIN":      Net("AD8317_RFIN"),      # post-matching → AD8317 RFIN
        "LRA_PLUS":         Net("LRA_PLUS"),         # haptic motor positive
        "LRA_MINUS":        Net("LRA_MINUS"),        # haptic motor negative
    }
