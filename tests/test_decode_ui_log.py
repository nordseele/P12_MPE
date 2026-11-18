"""Checks for tools/decode_ui_log.py."""

from __future__ import annotations

import unittest

import plistlib

from tools.decode_ui_log import parse_events, read_mmon, read_tokens, summarize

HEX_LOG = """
12:00:01  SysEx  F0 7D 50 31 03 00 05 00 00 10 04 00 01 55 00 01 F7
12:00:02  Control  B0 63 00
12:00:03  SysEx  F0 7D 50 31 02 01 0C 00 01 01 00 00 04 0B 00 01 F7
"""
DECIMAL_LOG = "print: 240 125 80 49 1 2 44 0 0 1 0 0 0 0 0 0 247"


class DecodeUiLogTests(unittest.TestCase):
    def test_hex_log(self) -> None:
        events = parse_events(read_tokens(HEX_LOG, None))
        self.assertEqual(len(events), 2)
        pot, encoder = events
        self.assertEqual((pot.kind, pot.control, pot.value), (3, 0x05, 0x210))
        self.assertEqual((pot.scope, pot.param, pot.count), (1, 0x55, 1))
        self.assertEqual((encoder.kind, encoder.control, encoder.value), (2, 0x0C, -1))
        self.assertEqual((encoder.scope, encoder.param), (4, 0x0B))

    def test_decimal_log(self) -> None:
        events = parse_events(read_tokens(DECIMAL_LOG, None))
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].kind, events[0].control, events[0].value), (1, 0x2C, 1))

    def test_summary_groups_by_control(self) -> None:
        events = parse_events(read_tokens(HEX_LOG + HEX_LOG, None))
        controls = summarize(events)
        self.assertEqual(len(controls), 2)
        self.assertEqual(controls[(3, 0x05)].events, 2)


def make_mmon(payloads: list[bytes]) -> bytes:
    """Build a minimal MIDI Monitor document with SysEx messages."""
    objects: list = ["$null", {"NS.objects": []}]
    for payload in payloads:
        data_index = len(objects) + 1
        objects[1]["NS.objects"].append(plistlib.UID(len(objects)))
        objects.append({"statusByte": 0xF0, "data": plistlib.UID(data_index)})
        objects.append({"NS.data": payload})
    archive = {"$version": 100000, "$archiver": "NSKeyedArchiver",
               "$top": {"root": plistlib.UID(1)}, "$objects": objects}
    archive_bytes = plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)
    return plistlib.dumps({"messageData": archive_bytes}, fmt=plistlib.FMT_BINARY)


class ReadMmonTests(unittest.TestCase):
    def test_sysex_payloads_are_reframed(self) -> None:
        payload = bytes.fromhex("7D50310300050000100400015500 01".replace(" ", ""))
        events = parse_events(read_mmon(make_mmon([payload, payload])))
        self.assertEqual(len(events), 2)
        self.assertEqual((events[0].kind, events[0].control, events[0].param), (3, 0x05, 0x55))


if __name__ == "__main__":
    unittest.main()
