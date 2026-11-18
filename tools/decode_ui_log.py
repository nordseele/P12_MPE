#!/usr/bin/env python3
# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
"""Decode a MIDI log recorded from the UI debug image into a control table.

The UI debug image (tools/make_mpe_image.py --ui-debug) sends one SysEx per
front-panel event:

    F0 7D 50 31 kind seq id_lo id_hi sign m0 m1 m2 scope p_lo p_hi count F7

Input is any text log that contains the message bytes, in hexadecimal
(MIDI Monitor, "F0 7D 50 ...") or in decimal (a Max [print], "240 125 80 ...").
Other MIDI traffic in the log is ignored.

For each control (kind, id), the output lists:
- the event count;
- the value range;
- the program or global parameter that the stock firmware changed, with its
  name taken from the metadata tables in the canonical image.

Controls that changed no parameter, mostly navigation buttons, keep their
order of first appearance. A --names file (one name per line) labels them in
that order, so the user can press them in a known order and list the names.
"""

from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
import re
import struct

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_IMAGE = REPOSITORY_ROOT / "firmware/extracted/P12_Main_1_4_2_3.bin"
BASE = 0x9D000000
LAYER_METADATA = 0x9D068AC0
GLOBAL_METADATA = 0x9D0688F8
HEADER = (0xF0, 0x7D, 0x50, 0x31)
FRAME_LENGTH = 17
KINDS = {1: "BTN", 2: "ENC", 3: "POT"}
SCOPES = {1: "layer A", 2: "layer B", 3: "layer A+B", 4: "global"}


@dataclass
class Event:
    kind: int
    seq: int
    control: int
    value: int
    scope: int
    param: int
    count: int


@dataclass
class Control:
    kind: int
    control: int
    events: int = 0
    low: int = 0
    high: int = 0
    params: Counter = field(default_factory=Counter)


def read_tokens(text: str, decimal: bool | None) -> list[int]:
    words = re.findall(r"\b(?:0x)?[0-9A-Fa-f]{1,3}\b", text)
    if decimal is None:
        decimal = any(word == "240" for word in words)
    values = []
    for word in words:
        try:
            value = int(word, 10 if decimal else 16)
        except ValueError:
            continue
        if 0 <= value <= 0xFF:
            values.append(value)
    return values


def read_mmon(data: bytes) -> list[int]:
    """Return the SysEx bytes stored in a MIDI Monitor (.mmon) document.

    The document is a binary plist whose "messageData" is an NSKeyedArchiver
    archive of SMMessage objects. SysEx payloads are stored without F0/F7.
    """
    import plistlib

    archive = plistlib.loads(plistlib.loads(data)["messageData"])
    objects = archive["$objects"]
    root = objects[archive["$top"]["root"].data]
    tokens: list[int] = []
    for uid in root["NS.objects"]:
        message = objects[uid.data]
        if not isinstance(message, dict) or message.get("statusByte") != 0xF0:
            continue
        payload = objects[message["data"].data]["NS.data"]
        tokens += [0xF0, *payload, 0xF7]
    return tokens


def parse_events(tokens: list[int]) -> list[Event]:
    events = []
    index = 0
    while index + FRAME_LENGTH <= len(tokens):
        if tuple(tokens[index : index + 4]) == HEADER and tokens[index + 16] == 0xF7:
            frame = tokens[index : index + FRAME_LENGTH]
            if all(byte < 0x80 for byte in frame[4:16]):
                magnitude = frame[9] | frame[10] << 7 | frame[11] << 14
                events.append(
                    Event(
                        kind=frame[4],
                        seq=frame[5],
                        control=frame[6] | frame[7] << 7,
                        value=-magnitude if frame[8] else magnitude,
                        scope=frame[12],
                        param=frame[13] | frame[14] << 7,
                        count=frame[15],
                    )
                )
                index += FRAME_LENGTH
                continue
        index += 1
    return events


def metadata_name(image: bytes, table: int, index: int) -> str:
    offset = table - BASE + 12 * index
    if offset + 12 > len(image):
        return "?"
    name_pointer = struct.unpack_from("<I", image, offset + 4)[0] - BASE
    if not 0 <= name_pointer < len(image):
        return "?"
    end = image.find(b"\0", name_pointer)
    name = image[name_pointer:end].decode("latin-1")
    return name if name.isprintable() and name else "(unnamed)"


def parameter_label(image: bytes | None, scope: int, param: int) -> str:
    if scope == 0:
        return ""
    table = GLOBAL_METADATA if scope == 4 else LAYER_METADATA
    name = metadata_name(image, table, param) if image else "?"
    return f"{SCOPES.get(scope, scope)} 0x{param:03x} {name}"


def summarize(events: list[Event]) -> "OrderedDict[tuple[int, int], Control]":
    controls: OrderedDict[tuple[int, int], Control] = OrderedDict()
    for event in events:
        key = (event.kind, event.control)
        control = controls.get(key)
        if control is None:
            control = controls[key] = Control(event.kind, event.control, low=event.value, high=event.value)
        control.events += 1
        control.low = min(control.low, event.value)
        control.high = max(control.high, event.value)
        if event.scope:
            control.params[(event.scope, event.param)] += 1
    return controls


def dropped_messages(events: list[Event]) -> int:
    dropped = 0
    for previous, current in zip(events, events[1:]):
        dropped += (current.seq - previous.seq - 1) % 128
    return dropped


def render(controls, image: bytes | None, names: list[str]) -> str:
    lines = [
        "| Kind | Id | Events | Values | Parameter changed (most frequent) | Name |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    unnamed = iter(names)
    for control in controls.values():
        if control.params:
            (scope, param), _ = control.params.most_common(1)[0]
            label = parameter_label(image, scope, param)
            name = ""
        else:
            label = ""
            name = next(unnamed, "")
        values = f"{control.low}" if control.low == control.high else f"{control.low}..{control.high}"
        lines.append(
            f"| {KINDS.get(control.kind, control.kind)} | 0x{control.control:02x} "
            f"| {control.events} | {values} | {label} | {name} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", type=Path, help="text log with the SysEx bytes")
    parser.add_argument("--names", type=Path, help="names, one per line, for controls without a parameter")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--decimal", action="store_true", help="bytes are decimal (Max [print])")
    group.add_argument("--hex", action="store_true", help="bytes are hexadecimal (MIDI Monitor)")
    args = parser.parse_args()

    decimal = True if args.decimal else False if args.hex else None
    raw = args.log.read_bytes()
    if raw.startswith(b"bplist00"):
        tokens = read_mmon(raw)
    else:
        tokens = read_tokens(raw.decode("utf-8", errors="replace"), decimal)
    events = parse_events(tokens)
    image = CANONICAL_IMAGE.read_bytes() if CANONICAL_IMAGE.exists() else None
    names = []
    if args.names:
        names = [line.strip() for line in args.names.read_text().splitlines() if line.strip()]
    controls = summarize(events)
    print(f"{len(events)} events, {len(controls)} controls, {dropped_messages(events)} dropped messages\n")
    print(render(controls, image, names))


if __name__ == "__main__":
    main()
