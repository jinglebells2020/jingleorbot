# Wearable All-Day Recorder — Design

**Date:** 2026-07-12
**Status:** Draft — pending user review
**Goal:** Wear the MOMA wireless lav mic all day; the ESP32-S3 records everything to microSD; at night the Mac turns it into a transcript.

## Overview

The MOMA transmitter clips near the mouth. Its USB-C receiver plugs into the
ESP32-S3-DevKitC's USB-OTG port. The ESP32 acts as USB host (playing the role a
phone normally plays), reads the receiver as a USB Audio Class microphone, and
writes WAV chunks to a microSD card. A 5V wireless-charging battery pack powers
everything through the DevKitC's 5V pin. At the end of the day the microSD goes
into the Mac, where a script transcribes all new audio with Whisper into one
daily transcript.

The ESP32 cannot replace the receiver dongle (the MOMA uses a proprietary
2.4 GHz radio protocol the ESP32's WiFi/BLE radio cannot demodulate), so the
dongle stays in the chain.

## Hardware

| Part | Role | Notes |
|---|---|---|
| ESP32-S3-DevKitC (generic) | Recorder brain, USB host | Already owned |
| MOMA USB-C receiver | Digital mic source (UAC) | Already owned |
| microSD module (SPI) + 32 GB card | Storage, ~11 days capacity | ~$2 module; 6 wires |
| 5V wireless-charging battery pack | Power | Already wired to 5V/GND pins |

### Wiring

microSD module on SPI2 (FSPI) default pins:

| SD module | DevKitC pin |
|---|---|
| VCC | 3V3 |
| GND | GND |
| CS  | GPIO10 |
| MOSI | GPIO11 |
| SCK | GPIO12 |
| MISO | GPIO13 |

Battery: pack `+` → 5V pin, pack `−` → GND (already done; feeds the onboard
regulator, correct for this board).

Receiver: plugs into the **USB** port (the OTG one, GPIO19/20), not the UART
port.

### VBUS power mod (required)

In host mode the DevKitC does not automatically supply 5V out of the USB-OTG
connector. The board revision determines the fix — verify before building
firmware:

- Newer DevKitC-1 revisions: bridge the **USB-OTG / VBUS** solder jumper pads.
- Otherwise: bridge the diode between the 5V rail and the connector's VBUS, or
  splice the battery's 5V directly into the receiver's cable via a USB-C
  breakout board.

Acceptance check: receiver's power LED turns on when plugged into the ESP32
with no other power source attached to the receiver.

## Firmware (ESP-IDF)

### Phase 0 — compatibility spike (go/no-go gate)

A throwaway sketch using `usb_host` that enumerates the receiver and prints its
descriptors. **Pass:** the receiver exposes a USB Audio Class 1.0 input
streaming interface. **Fail (UAC 2.0-only or vendor-specific):** abandon the
USB path and fall back to an INMP441 I2S mic (see Fallbacks). Nothing else gets
built until this passes.

### Recorder application

- `usb_host_uac` component opens the receiver's input stream at its native
  format (expected 48 kHz / 16-bit; mono preferred, downmix if stereo).
- Audio flows through a ring buffer (in PSRAM if available, else DRAM) into a
  WAV writer task, decoupling USB callbacks from SD write latency spikes.
- **10-minute WAV chunks**, so a crash or dead battery loses at most 10 minutes
  and files stay Whisper-friendly. Header finalized on rotate; on boot, any
  truncated last chunk is repaired (header patched from file size).
- Filenames: `REC_<bootcount>_<chunk>_<timestamp-or-seq>.wav`. Clock via SNTP
  when home WiFi is reachable at boot, persisted through deep-sleep-safe RTC;
  otherwise sequence numbers only.
- Receiver disconnect/reconnect: close chunk cleanly, poll for the device,
  resume with a new chunk. Same for SD write errors (retry, then blink error).
- Status LED (onboard RGB): slow blink = recording, fast blink = no
  receiver/SD error.

Storage math: 48 kHz × 16-bit mono ≈ 5.5 MB/min ≈ 330 MB/h ≈ 5.3 GB per
16-hour day. 32 GB card ≈ 5–6 days between offloads. (Optional v2: record at
16 kHz to triple that; Whisper needs no more.)

## Mac transcription pipeline

`transcribe_day.sh` (plus a small Python helper), run when the SD card is
inserted:

1. Copy new `REC_*.wav` files to `~/Recordings/<date>/` (tracked by a manifest
   of already-imported files).
2. Transcribe each chunk with local Whisper (`mlx-whisper`, Apple-Silicon
   fast; `whisper.cpp` as alternative).
3. Concatenate into `~/Recordings/<date>/transcript.md` with per-chunk time
   headings.
4. Optionally free the card (delete imported files after successful copy).

No cloud services; audio never leaves the Mac.

## Power budget

Estimated draw: ESP32-S3 recording ≈ 90–150 mA, receiver over VBUS ≈
50–100 mA → ~150–250 mA total at 5V. A 16-hour day needs **≥ 3000–4000 mAh**
at 5V. The current pack's capacity is unknown — measure runtime in the
overnight soak test; if short, swap to a bigger pack (wireless charging is a
nice-to-have, not a requirement).

## Testing

1. **Spike:** descriptor dump shows UAC 1.0 input stream (go/no-go).
2. **Bench:** 1-hour recording, play back on the Mac, verify no gaps/clicks.
3. **Soak:** overnight recording on battery only; measures real battery life
   and catches chunk-rotation bugs.
4. **Abuse:** yank the receiver mid-recording, power-cycle mid-chunk — verify
   recovery and that prior chunks stay playable.
5. **Pipeline:** full day of chunks through `transcribe_day.sh` → readable
   transcript.

## Fallbacks & risks

| Risk | Likelihood | Fallback |
|---|---|---|
| Receiver is not UAC 1.0 | Medium | INMP441 I2S mic wired to ESP32 (loses lav quality; firmware simpler) |
| VBUS mod too fiddly | Low | USB-C breakout + spliced 5V |
| Battery too small | Medium | Bigger 5V pack |
| SD SPI too slow at 48 kHz | Low | Larger ring buffer; or 16 kHz capture |

## Out of scope (v2 ideas)

- Auto-upload over home WiFi to the Pi/Mac (no card shuffling).
- Voice-activity detection to skip silence.
- Opus/ADPCM compression, speaker diarization, daily summary via LLM.
- Enclosure/case design.

## Legal note

All-day recording captures other people's voices. Recording-consent law varies
by country/state — the wearer is responsible for using this lawfully.
