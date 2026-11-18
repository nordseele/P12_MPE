#!/usr/bin/env python3
# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
"""Set the MPE Global settings over NRPN (P12-test-10 and later).

  python3 tools/mpe_nrpn.py voices=12 y=matrix [--port mio]

NRPN 433 + setting index, on channel 1 (firmware/patches/mpe/mpe.c,
NRPN_SETTINGS; research/nrpn_map_firmware.md). Applied at once, like the
Global menu; saved with the Global settings. Values as in the menu:

  mode     off | on                     wheel     bend-all | off
  voices   single | pair | 12           fallback  play | silent
  bend     0 (Program) - 96 semitones   layered   fallback | mpe   (Stack/Split)
  y        off | matrix | matrix+cut    arp       per-note | fallback
           (MPE Y > Timbre)

Images before P12-pt-04 had ten settings (bright, breath, express at 436-438).
"""

from __future__ import annotations

import argparse

import mido

FIRST = 433
SETTINGS = {  # name: (index, value names in order 0, 1, 2)
    "mode": (0, ["off", "on"]), "voices": (1, ["single", "pair", "12"]), "bend": (2, None),
    "y": (3, ["off", "matrix", "matrix+cut"]),
    "wheel": (4, ["bend-all", "off"]), "fallback": (5, ["play", "silent"]),
    "layered": (6, ["fallback", "mpe"]), "arp": (7, ["per-note", "fallback"]),
}


def parse(item: str) -> tuple[int, int]:
    name, _, value = item.partition("=")
    index, names = SETTINGS[name]
    return FIRST + index, int(value) if names is None else names.index(value.lower())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("settings", nargs="+", help="name=value")
    ap.add_argument("--port", default="mio")
    args = ap.parse_args()
    pairs = [parse(s) for s in args.settings]
    assert all(v < 128 for _, v in pairs)  # all checked before anything is sent
    with mido.open_output(args.port) as port:
        for number, value in pairs:
            # CC 38 first: every value fits in 7 bits, so CC 6 then resubmits the same value
            for cc, v in ((99, number >> 7), (98, number & 0x7F), (38, value & 0x7F), (6, value >> 7)):
                port.send(mido.Message("control_change", channel=0, control=cc, value=v))
        for cc in (101, 100):  # RPN null
            port.send(mido.Message("control_change", channel=0, control=cc, value=127))
    print("sent", ", ".join(args.settings))


if __name__ == "__main__":
    main()
