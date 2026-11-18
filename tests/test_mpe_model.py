# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
"""Deterministic vectors for the Prophet 12 MPE model, revision 2 (per DSP)."""

from __future__ import annotations

import unittest

from tools.mpe_model import (
    DSP_COUNT,
    NO_CHANNEL,
    NO_DSP,
    NO_NOTE,
    PITCH_CENTER,
    TIMBRE_NEUTRAL,
    Compatibility,
    MpeModel,
)


def enable_mpe(model: MpeModel, members: int = 15, transport: str = "din") -> None:
    model.process(0xB0, 101, 0, transport=transport)
    model.process(0xB0, 100, 6, transport=transport)
    model.process(0xB0, 6, members, transport=transport)
    model.drain_actions()


def actions_of(model: MpeModel, kind: str):
    return [action for action in model.actions if action.kind == kind]


def sent(model: MpeModel):
    return [
        (a.details["target"], a.details["control"], a.details["value"])
        for a in actions_of(model, "dsp_expression")
    ]


class MpeModelTests(unittest.TestCase):
    def test_mcm_enable_disable_and_transport_state_are_independent(self) -> None:
        model = MpeModel()
        model.process(0xB0, 101, 0, transport="din")
        model.process(0xB0, 100, 6, transport="din")
        model.process(0xB0, 6, 15, transport="usb")
        self.assertFalse(model.enabled)
        model.process(0xB0, 6, 15, transport="din")
        self.assertTrue(model.enabled)
        model.process(0xB0, 6, 0, transport="din")
        self.assertFalse(model.enabled)

    def test_note_on_uses_primary_voice_and_sends_expression_first(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        model.process(0x91, 60, 100)
        kinds = [a.kind for a in model.actions]
        self.assertLess(kinds.index("dsp_expression"), kinds.index("voice_activate"))
        dsp = model.member_dsp[1]
        self.assertEqual(model.voice_note[2 * dsp], 60)
        self.assertEqual(model.voice_age[2 * dsp + 1], 0)
        self.assertEqual(
            sent(model),
            [(dsp, "pitch_bend", 8192), (dsp, "channel_pressure", 0), (dsp, "cc74", 64)],
        )

    def test_six_notes_use_six_distinct_dsps(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        for channel in range(1, 7):
            model.process(0x90 | channel, 59 + channel, 100)
        self.assertEqual(sorted(model.member_dsp[1:7]), list(range(DSP_COUNT)))
        self.assertEqual(actions_of(model, "dsp_stolen"), [])

    def test_seventh_note_steals_the_oldest_dsp(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        for channel in range(1, 7):
            model.process(0x90 | channel, 59 + channel, 100)
        first_dsp = model.member_dsp[1]
        model.drain_actions()

        model.process(0x97, 72, 100)
        stolen = actions_of(model, "dsp_stolen")
        self.assertEqual(stolen[0].details, {"dsp": first_dsp, "old_channel": 1})
        self.assertEqual(model.dsp_owner[first_dsp], 7)
        self.assertEqual(model.member_dsp[1], NO_DSP)
        self.assertEqual(model.member_note[1], 60)  # held record kept

    def test_stale_note_off_after_steal_does_not_release_new_owner(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        for channel in range(1, 8):
            model.process(0x90 | channel, 59 + channel, 100)
        dsp = model.member_dsp[7]
        model.drain_actions()

        model.process(0x81, 60, 0)
        self.assertEqual(actions_of(model, "voice_release"), [])
        self.assertEqual(model.dsp_owner[dsp], 7)
        self.assertEqual(model.member_note[1], NO_NOTE)

    def test_expression_before_note_is_cached_and_applied(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        model.process(0xE2, 1, 96)  # 12289
        model.process(0xD2, 73)
        model.process(0xB2, 74, 99)
        self.assertEqual(sent(model), [])
        model.process(0x92, 64, 110)
        dsp = model.member_dsp[2]
        self.assertEqual(
            sent(model),
            [(dsp, "pitch_bend", 12289), (dsp, "channel_pressure", 73), (dsp, "cc74", 99)],
        )

    def test_member_expression_targets_only_its_dsp_with_stock_word(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        model.process(0x91, 60, 100)
        model.process(0x92, 67, 100)
        model.drain_actions()

        model.process(0xE1, 0, 32)
        model.process(0xB2, 74, 77)
        actions = actions_of(model, "dsp_expression")
        self.assertEqual(actions[0].details["target"], model.member_dsp[1])
        self.assertEqual(actions[0].details["command"], 0xC1FC0000 | 4096)
        self.assertEqual(actions[1].details["target"], model.member_dsp[2])
        self.assertEqual(actions[1].details["command"], 0xC1FB0000 | 77)

    def test_manager_bend_is_combined_into_every_owned_dsp(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        model.process(0x91, 60, 100)
        model.process(0x92, 67, 100)
        model.process(0xE1, 0, 72)  # member 1: 9216 (+1024)
        model.drain_actions()

        model.process(0xE0, 0, 80)  # manager: 10240 (+2048)
        self.assertEqual(
            sorted(sent(model)),
            sorted(
                [
                    (model.member_dsp[1], "pitch_bend", 9216 + 2048),
                    (model.member_dsp[2], "pitch_bend", 8192 + 2048),
                ]
            ),
        )
        self.assertEqual(actions_of(model, "legacy_event"), [])

    def test_combined_pitch_is_clamped(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        model.process(0x91, 60, 100)
        model.process(0xE1, 127, 127)
        model.process(0xE0, 127, 127)
        self.assertEqual(sent(model)[-1][2], 0x3FFF)

    def test_manager_pressure_and_cc74_are_consumed(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        model.process(0x91, 60, 100)
        model.drain_actions()
        model.process(0xD0, 90)
        model.process(0xB0, 74, 20)
        self.assertEqual(sent(model), [])
        self.assertEqual(actions_of(model, "legacy_event"), [])
        self.assertEqual(len(actions_of(model, "manager_expression_ignored")), 2)

    def test_duplicate_pitch_note_off_releases_only_its_owner(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        model.process(0x91, 60, 100)
        model.process(0x92, 60, 100)
        first, second = model.member_dsp[1], model.member_dsp[2]
        self.assertNotEqual(first, second)

        model.process(0x81, 60, 0)
        self.assertEqual(model.voice_age[2 * first], 0)
        self.assertNotEqual(model.voice_age[2 * second], 0)
        self.assertTrue(model.note_present[60])
        model.process(0x82, 60, 0)
        self.assertFalse(model.note_present[60])

    def test_second_note_on_same_member_implicitly_releases_first(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        model.process(0x93, 48, 100)
        model.drain_actions()
        model.process(0x93, 49, 100)
        releases = actions_of(model, "voice_release")
        self.assertEqual(releases[0].details["reason"], "implicit_retrigger")
        self.assertEqual(model.member_note[3], 49)
        self.assertFalse(model.note_present[48])

    def test_pair_mode_activates_and_releases_both_voices(self) -> None:
        model = MpeModel(pair_mode=True)
        enable_mpe(model)
        model.process(0x91, 60, 100)
        dsp = model.member_dsp[1]
        self.assertEqual(model.voice_note[2 * dsp], 60)
        self.assertEqual(model.voice_note[2 * dsp + 1], 60)
        model.process(0x81, 60, 0)
        self.assertEqual(model.voice_age[2 * dsp], 0)
        self.assertEqual(model.voice_age[2 * dsp + 1], 0)

    def test_manager_sustain_reset_and_all_notes_off(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        model.process(0x91, 60, 100)
        model.process(0xE1, 0, 100)
        model.process(0xE0, 0, 100)
        model.drain_actions()

        model.process(0xB0, 64, 127)
        self.assertEqual(actions_of(model, "legacy_event")[0].details["data1"], 64)
        model.process(0xB0, 121, 0)
        self.assertEqual(model.member_pitch[1], PITCH_CENTER)
        self.assertEqual(model.manager_pitch, PITCH_CENTER)
        self.assertIn((model.member_dsp[1], "pitch_bend", 8192), sent(model))
        model.process(0xB0, 123, 0)
        self.assertTrue(all(age == 0 for age in model.voice_age))
        self.assertTrue(all(owner == NO_CHANNEL for owner in model.dsp_owner))
        self.assertTrue(model.enabled)

    def test_member_reset_and_all_notes_off_are_channel_scoped(self) -> None:
        model = MpeModel()
        enable_mpe(model)
        model.process(0x91, 60, 100)
        model.process(0x92, 64, 100)
        first, second = model.member_dsp[1], model.member_dsp[2]
        model.process(0xB1, 74, 100)
        model.drain_actions()

        model.process(0xB1, 121, 0)
        self.assertEqual(
            sent(model),
            [(first, "pitch_bend", 8192), (first, "channel_pressure", 0), (first, "cc74", 64)],
        )
        self.assertEqual(model.member_timbre[1], TIMBRE_NEUTRAL)
        model.process(0xB1, 123, 0)
        self.assertEqual(model.voice_age[2 * first], 0)
        self.assertNotEqual(model.voice_age[2 * second], 0)

    def test_enable_boundary_releases_legacy_voices(self) -> None:
        model = MpeModel()
        model.voice_age[3] = 1
        model.voice_note[3] = 50
        enable_mpe(model)
        self.assertEqual(model.voice_age[3], 0)

    def test_disabled_path_preserves_legacy_event_bytes(self) -> None:
        model = MpeModel()
        vectors = [(0x91, 60, 100), (0x81, 60, 64), (0xE4, 3, 70), (0xD7, 45, 0), (0xBA, 74, 91)]
        for status, data1, data2 in vectors:
            model.process(status, data1, data2, transport="usb")
        self.assertEqual(
            [
                (a.details["status"], a.details["data1"], a.details["data2"])
                for a in actions_of(model, "legacy_event")
            ],
            vectors,
        )

    def test_incompatible_modes_reject_activation(self) -> None:
        cases = [
            (Compatibility(normal_mode=False), "split_or_stack"),
            (Compatibility(polyphonic=False), "unison"),
            (Compatibility(arpeggiator=True), "arpeggiator"),
            (Compatibility(multi_mode=True), "multi_mode"),
            (Compatibility(global_channel=1), "global_channel_not_one"),
            (Compatibility(local_control=True), "local_control"),
        ]
        for compatibility, reason in cases:
            with self.subTest(reason=reason):
                model = MpeModel(compatibility)
                model.configure(15)
                self.assertFalse(model.enabled)
                self.assertEqual(actions_of(model, "mpe_rejected")[-1].details["reason"], reason)


if __name__ == "__main__":
    unittest.main()
