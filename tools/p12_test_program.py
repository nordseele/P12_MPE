#!/usr/bin/env python3
# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
"""Send the MPE test program to the Prophet 12 edit buffer over NRPN (not saved).

  python3 tools/p12_test_program.py [--stack] [--port mio] [--dry-run]

Before: on the P12, load the Basic Patch (Global menu), then set Global
MIDI Channel = 1 and MIDI Param Receive = NRPN (or All). The script only
changes the parameters below, from the corrected NRPN map in
reference/user_manual/Prophet_12_Complete_Reference_OS_1.4.2.3.md (Layer B =
Layer A + 512), checked against the firmware table
(research/nrpn_map_firmware.md). Save the program on the synth afterwards if you want to keep
it.

Sound: one sawtooth (Osc 1), no slop, no unison, mono, no delay, gate
envelope, low-pass at a low cutoff (Y opens it through MPE Y > Bright).
Mod slot 1: Aftertouch -> Osc All Freq, small amount, so pressure on one of
two equal notes is heard as beating (tools/mpe_scenario.py voice-pressure).
--stack: A/B Mode = Stack, Layer B the same one octave up.
"""

from __future__ import annotations

import argparse
import time

AFTERTOUCH, OSC_ALL_FREQ = 19, 5  # research/program_helpers.md, "Modulation sources and destinations"
NAME = "MPE Test 09"
NAME_FIRST = 483  # firmware 1.4.2.3 table; the OS 1.4.1 map says 480 (research/nrpn_map_firmware.md)


def layer(octave_up: bool) -> dict[int, int]:
    p = {}
    for base in (0, 18, 36, 54):  # Osc 1-4
        p[base + 2] = 0  # level
        p[base + 7] = p[base + 8] = 0  # FM, AM
        p[base + 9] = 0  # slop
        p[base + 10] = 0  # glide
        p[base + 11] = 0  # sync
    p[0] = 36 if octave_up else 24  # Osc 1 Pitch, C3 / C2
    p[1] = 50  # fine tune centre
    p[2] = 110  # Osc 1 Level
    p[3] = 1  # Sawtooth
    p[4] = 64  # Shape Mod centre
    p[12] = 1  # key follow
    p.update({72: 0, 74: 0})  # sub osc, glide off
    p.update({82: 0, 83: 0, 84: 0})  # hack, decimate, drive
    p.update({90: 40, 91: 30, 92: 0, 93: 1})  # LPF: low cutoff, some resonance, no key, 4-pole
    p.update({94: 0, 95: 0, 96: 0})  # HPF off
    p.update({97: 127, 99: 110, 100: 0, 101: 0})  # feedback 0, voice volume, pan spread 0, distortion 0
    p.update({103: 127, 104: 0, 105: 0, 106: 0, 107: 0, 108: 127, 109: 30, 110: 0})  # VCA env: gate
    p.update({114: 127, 115: 0})  # LPF env amount 0
    p.update({125: 127, 133: 0, 136: 127, 144: 0})  # Env 3/4 amount 0, no destination
    for lfo in (151, 161, 171, 181):
        p[lfo] = 0  # amount
        p[lfo + 4] = 0  # destination
    for delay in (190, 198, 206, 214):
        p[delay] = 0  # amount
    for slot in range(16):
        n = 219 + 4 * slot
        p.update({n: 0, n + 1: 127, n + 2: 0})  # source Off, amount 0, destination Off
    p.update({219: AFTERTOUCH, 220: 127 + 12, 221: OSC_ALL_FREQ})
    p.update({283: 0, 289: 0})  # unison off, arp off
    for i, ch in enumerate(NAME.ljust(17)):
        p[NAME_FIRST + i] = ord(ch)  # chars 18-19 (NRPN 500-501) are Layer Program/Bank Change
    return p


def program(stack: bool) -> list[tuple[int, int]]:
    out = list(layer(False).items())
    if stack:
        out += [(n + 512, v) for n, v in layer(True).items()]
    out.append((800, 2 if stack else 0))  # A/B Mode, global: no layer offset
    return out


def nrpn(channel: int, number: int, value: int) -> list[tuple[int, int, int]]:
    return [(channel, 99, number >> 7), (channel, 98, number & 0x7F),
            (channel, 6, value >> 7), (channel, 38, value & 0x7F)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stack", action="store_true")
    ap.add_argument("--port", default="mio")
    ap.add_argument("--dry-run", action="store_true", help="print the NRPNs, send nothing")
    args = ap.parse_args()
    params = program(args.stack)
    assert all(0 <= n < 1024 and 0 <= v < 256 for n, v in params)
    if args.dry_run:
        for n, v in params:
            print(n, v)
        return
    import mido
    with mido.open_output(args.port) as port:
        for n, v in params:
            for ch, cc, val in nrpn(0, n, v):
                port.send(mido.Message("control_change", channel=ch, control=cc, value=val))
            time.sleep(0.005)  # DIN pacing
        for cc in (101, 100):  # RPN null: a stray Data Entry no longer hits the last parameter
            port.send(mido.Message("control_change", channel=0, control=cc, value=127))
    print(f"sent {len(params)} parameters to the edit buffer ({'Stack' if args.stack else 'Normal'}); "
          "save on the synth to keep it")


if __name__ == "__main__":
    main()
