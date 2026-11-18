#!/usr/bin/env python3
# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
"""Host-side reference model for the Prophet 12 MPE design, revision 2.

This is an executable specification of ``research/mpe_design.md`` (six MPE
notes, one per DSP), not a firmware emulator.  The per-DSP expression words it
emits were confirmed on hardware on 2026-11-13 (research/voice_bend_test.md);
the surrounding allocation and release behavior is a design to be tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


VOICE_COUNT = 12
DSP_COUNT = 6
CHANNEL_COUNT = 16
NOTE_COUNT = 128
NO_CHANNEL = 0xFF
NO_NOTE = 0xFF
NO_DSP = 0xFF
PITCH_CENTER = 0x2000
PITCH_MAX = 0x3FFF
TIMBRE_NEUTRAL = 64

EXPRESSION_WORDS = {
    "pitch_bend": 0xC1FC0000,
    "channel_pressure": 0xC1FA0000,
    "cc74": 0xC1FB0000,
}


@dataclass(frozen=True)
class Action:
    """One observable decision made by the reference model."""

    kind: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Compatibility:
    """Prophet mode restrictions for revision 2."""

    normal_mode: bool = True
    polyphonic: bool = True
    arpeggiator: bool = False
    multi_mode: bool = False
    global_channel: int = 0
    local_control: bool = False

    def rejection_reason(self) -> str | None:
        if not self.normal_mode:
            return "split_or_stack"
        if not self.polyphonic:
            return "unison"
        if self.arpeggiator:
            return "arpeggiator"
        if self.multi_mode:
            return "multi_mode"
        if self.global_channel != 0:
            return "global_channel_not_one"
        if self.local_control:
            return "local_control"
        return None


class MpeModel:
    """Reference state machine for a Lower Zone, one-note-per-DSP MPE layer."""

    def __init__(
        self, compatibility: Compatibility | None = None, *, pair_mode: bool = False
    ) -> None:
        self.compatibility = compatibility or Compatibility()
        self.pair_mode = pair_mode
        self.enabled = False
        self.member_count = 0
        self.actions: list[Action] = []

        # Native firmware tables (0xa0009a90 ages, 0xa0009ac0 notes).
        self.voice_age = [0] * VOICE_COUNT
        self.voice_note = [NO_NOTE] * VOICE_COUNT

        self.dsp_owner = [NO_CHANNEL] * DSP_COUNT
        self.dsp_cursor = 0
        self.member_dsp = [NO_DSP] * CHANNEL_COUNT
        self.member_note = [NO_NOTE] * CHANNEL_COUNT
        self.member_pitch = [PITCH_CENTER] * CHANNEL_COUNT
        self.member_pressure = [0] * CHANNEL_COUNT
        self.member_timbre = [TIMBRE_NEUTRAL] * CHANNEL_COUNT
        self.manager_pitch = PITCH_CENTER
        self.note_hold_count = [0] * NOTE_COUNT
        self.note_present = [False] * NOTE_COUNT

        self._rpn = {
            "din": {"msb": 0x7F, "lsb": 0x7F},
            "usb": {"msb": 0x7F, "lsb": 0x7F},
        }

    # ------------------------------------------------------------------ util
    def _emit(self, kind: str, **details: Any) -> None:
        self.actions.append(Action(kind, details))

    def drain_actions(self) -> list[Action]:
        actions = self.actions
        self.actions = []
        return actions

    def voices_of(self, dsp: int) -> list[int]:
        return [2 * dsp, 2 * dsp + 1] if self.pair_mode else [2 * dsp]

    # ------------------------------------------------------------ activation
    def configure(self, member_count: int, transport: str = "din") -> None:
        """Apply a decoded Lower Zone MPE Configuration Message."""
        if not 0 <= member_count <= 15:
            raise ValueError("MPE member count must be in range 0..15")

        if member_count == 0:
            self._release_owned_dsps(reason="mpe_disable")
            self._reset_tracking()
            self.enabled = False
            self.member_count = 0
            self._emit("mpe_disabled", transport=transport)
            return

        reason = self.compatibility.rejection_reason()
        if reason is not None:
            if self.enabled:
                self._release_owned_dsps(reason="incompatible_mode")
                self._reset_tracking()
            self.enabled = False
            self.member_count = 0
            self._emit("mpe_rejected", transport=transport, reason=reason)
            return

        if not self.enabled:
            self._release_all_voices(reason="mpe_enable_boundary")
        else:
            self._release_owned_dsps(reason="mpe_reconfigure")
        self._reset_tracking()
        self.enabled = True
        self.member_count = member_count
        self._emit("mpe_enabled", transport=transport, member_count=member_count)

    # -------------------------------------------------------------- dispatch
    def process(
        self,
        status: int,
        data1: int = 0,
        data2: int = 0,
        *,
        transport: str = "din",
    ) -> None:
        """Process one normalized MIDI 1.0 channel-voice message."""
        if transport not in self._rpn:
            raise ValueError(f"unsupported transport: {transport}")
        if not 0x80 <= status <= 0xEF:
            raise ValueError(f"not a channel-voice status: {status:#x}")
        if not 0 <= data1 <= 0x7F or not 0 <= data2 <= 0x7F:
            raise ValueError("MIDI data bytes must be in range 0..127")

        message = status & 0xF0
        channel = status & 0x0F

        if message == 0xB0 and self._handle_mcm_cc(transport, channel, data1, data2):
            self.assert_invariants()
            return

        if not self.enabled:
            self._legacy_event(transport, status, data1, data2)
        elif channel == 0:
            self._manager_event(transport, message, data1, data2)
        elif 1 <= channel <= self.member_count:
            self._member_event(transport, message, channel, data1, data2)
        else:
            self._emit("channel_rejected", transport=transport, channel=channel)

        self.assert_invariants()

    def _handle_mcm_cc(
        self, transport: str, channel: int, controller: int, value: int
    ) -> bool:
        if channel != 0:
            return False
        selected = self._rpn[transport]
        if controller == 101:
            selected["msb"] = value
            self._emit("rpn_select", transport=transport, part="msb", value=value)
            return True
        if controller == 100:
            selected["lsb"] = value
            self._emit("rpn_select", transport=transport, part="lsb", value=value)
            return True
        if selected == {"msb": 0, "lsb": 6} and controller == 6:
            self.configure(value, transport)
            return True
        if selected == {"msb": 0, "lsb": 6} and controller == 38:
            self._emit("mcm_data_lsb_ignored", transport=transport, value=value)
            return True
        return False

    def _legacy_event(self, transport: str, status: int, data1: int, data2: int) -> None:
        self._emit(
            "legacy_event", transport=transport, status=status, data1=data1, data2=data2
        )

    # --------------------------------------------------------------- manager
    def _manager_event(self, transport: str, message: int, data1: int, data2: int) -> None:
        status = message
        if message in (0x80, 0x90):
            self._emit("manager_note_ignored", note=data1)
            return
        if message == 0xE0:
            self.manager_pitch = data1 | (data2 << 7)
            for dsp in range(DSP_COUNT):
                owner = self.dsp_owner[dsp]
                if owner != NO_CHANNEL:
                    self._send_dsp(dsp, "pitch_bend", self._combined_pitch(owner))
            return
        if message == 0xD0 or (message == 0xB0 and data1 == 74):
            self._emit("manager_expression_ignored", status=status, data1=data1)
            return
        if message == 0xB0 and data1 in (120, 123):
            self._release_all_voices(reason=f"manager_cc{data1}")
            self._reset_tracking()
            self._emit("legacy_all_notes_off", controller=data1)
            return
        if message == 0xB0 and data1 == 121:
            self._legacy_event(transport, status, data1, data2)
            self._reset_expression_caches()
            for dsp in range(DSP_COUNT):
                owner = self.dsp_owner[dsp]
                if owner != NO_CHANNEL:
                    self._send_all_expression(dsp, owner)
            return
        self._legacy_event(transport, status, data1, data2)

    # ---------------------------------------------------------------- member
    def _member_event(
        self, transport: str, message: int, channel: int, data1: int, data2: int
    ) -> None:
        if message == 0x90 and data2 != 0:
            self._member_note_on(channel, data1, data2)
            return
        if message == 0x80 or (message == 0x90 and data2 == 0):
            self._member_note_off(channel, data1, reason="note_off")
            return
        if message == 0xE0:
            self.member_pitch[channel] = data1 | (data2 << 7)
            self._send_member(channel, "pitch_bend")
            return
        if message == 0xD0:
            self.member_pressure[channel] = data1
            self._send_member(channel, "channel_pressure")
            return
        if message == 0xB0 and data1 == 74:
            self.member_timbre[channel] = data2
            self._send_member(channel, "cc74")
            return
        if message == 0xB0 and data1 in (120, 123):
            note = self.member_note[channel]
            if note != NO_NOTE:
                self._member_note_off(channel, note, reason=f"member_cc{data1}")
            else:
                self._emit("member_all_notes_off", channel=channel, controller=data1)
            return
        if message == 0xB0 and data1 == 121:
            self.member_pitch[channel] = PITCH_CENTER
            self.member_pressure[channel] = 0
            self.member_timbre[channel] = TIMBRE_NEUTRAL
            dsp = self.member_dsp[channel]
            if dsp != NO_DSP:
                self._send_all_expression(dsp, channel)
            self._emit("member_controllers_reset", channel=channel)
            return
        self._emit(
            "member_event_consumed",
            transport=transport,
            status=message | channel,
            data1=data1,
            data2=data2,
        )

    def _member_note_on(self, channel: int, note: int, velocity: int) -> None:
        if self.member_note[channel] != NO_NOTE:
            self._member_note_off(
                channel, self.member_note[channel], reason="implicit_retrigger"
            )

        self.note_hold_count[note] = min(0xFF, self.note_hold_count[note] + 1)
        self.note_present[note] = True
        self.member_note[channel] = note

        # Legacy Note On path runs with member context; the allocator hook
        # replaces only the voice choice.
        dsp = self._choose_dsp()
        self.member_dsp[channel] = dsp
        self.dsp_owner[dsp] = channel
        self._send_all_expression(dsp, channel)  # before the note commands
        for voice in self.voices_of(dsp):
            self._activate_voice(voice, note, velocity)
        self._emit("member_note_on", channel=channel, note=note, dsp=dsp)

    def _member_note_off(self, channel: int, note: int, *, reason: str) -> None:
        if self.member_note[channel] != note:
            self._emit("stale_note_off", channel=channel, note=note)
            return

        dsp = self.member_dsp[channel]
        released: list[int] = []
        if (
            dsp != NO_DSP
            and self.dsp_owner[dsp] == channel
            and self.voice_note[2 * dsp] == note
        ):
            for voice in self.voices_of(dsp):
                if self._release_voice(voice, reason=reason):
                    released.append(voice)
            self.dsp_owner[dsp] = NO_CHANNEL
        self.member_dsp[channel] = NO_DSP
        self.member_note[channel] = NO_NOTE
        self._decrement_note_hold(note)
        self._emit("member_note_off", channel=channel, note=note, voices=released)

    # ------------------------------------------------------------ allocation
    def _choose_dsp(self) -> int:
        for offset in range(DSP_COUNT):
            dsp = (self.dsp_cursor + offset) % DSP_COUNT
            if self.dsp_owner[dsp] == NO_CHANNEL:
                self.dsp_cursor = (dsp + 1) % DSP_COUNT
                self._release_dsp_voices(dsp, reason="dsp_reuse")
                return dsp

        oldest = min(range(DSP_COUNT), key=lambda dsp: self.voice_age[2 * dsp])
        previous = self.dsp_owner[oldest]
        self._release_dsp_voices(oldest, reason="dsp_steal")
        self.member_dsp[previous] = NO_DSP
        self.dsp_owner[oldest] = NO_CHANNEL
        self._emit("dsp_stolen", dsp=oldest, old_channel=previous)
        self.dsp_cursor = (oldest + 1) % DSP_COUNT
        return oldest

    def _activate_voice(self, voice: int, note: int, velocity: int) -> None:
        """Model of ActivateVoiceForNote (0x9d01be30)."""
        self.voice_note[voice] = note
        self.voice_age[voice] = max(self.voice_age) + 1
        self._emit("voice_activate", voice=voice, note=note, velocity=velocity)

    def _release_voice(self, voice: int, *, reason: str) -> bool:
        """Model of ReleaseVoiceAndCompactAges (0x9d01c6c8)."""
        age = self.voice_age[voice]
        if age == 0:
            return False
        note = self.voice_note[voice]
        self.voice_age[voice] = 0
        self.voice_note[voice] = NO_NOTE
        for index, other_age in enumerate(self.voice_age):
            if other_age > age:
                self.voice_age[index] = other_age - 1
        self._emit("voice_release", voice=voice, note=note, reason=reason)
        return True

    def _release_dsp_voices(self, dsp: int, *, reason: str) -> None:
        for voice in (2 * dsp, 2 * dsp + 1):
            self._release_voice(voice, reason=reason)

    def _release_owned_dsps(self, *, reason: str) -> None:
        for dsp in range(DSP_COUNT):
            if self.dsp_owner[dsp] != NO_CHANNEL:
                self._release_dsp_voices(dsp, reason=reason)

    def _release_all_voices(self, *, reason: str) -> None:
        for voice in range(VOICE_COUNT):
            self._release_voice(voice, reason=reason)

    def _decrement_note_hold(self, note: int) -> None:
        if self.note_hold_count[note] == 0:
            raise AssertionError(f"note hold count underflow for note {note}")
        self.note_hold_count[note] -= 1
        if self.note_hold_count[note] == 0:
            self.note_present[note] = False

    def _reset_expression_caches(self) -> None:
        self.member_pitch = [PITCH_CENTER] * CHANNEL_COUNT
        self.member_pressure = [0] * CHANNEL_COUNT
        self.member_timbre = [TIMBRE_NEUTRAL] * CHANNEL_COUNT
        self.manager_pitch = PITCH_CENTER

    def _reset_tracking(self) -> None:
        self.dsp_owner = [NO_CHANNEL] * DSP_COUNT
        self.member_dsp = [NO_DSP] * CHANNEL_COUNT
        self.member_note = [NO_NOTE] * CHANNEL_COUNT
        self.note_hold_count = [0] * NOTE_COUNT
        self.note_present = [False] * NOTE_COUNT
        self._reset_expression_caches()

    # ------------------------------------------------------------ expression
    def _combined_pitch(self, channel: int) -> int:
        value = self.member_pitch[channel] + self.manager_pitch - PITCH_CENTER
        return max(0, min(PITCH_MAX, value))

    def _member_value(self, channel: int, control: str) -> int:
        if control == "pitch_bend":
            return self._combined_pitch(channel)
        if control == "channel_pressure":
            return self.member_pressure[channel]
        return self.member_timbre[channel]

    def _send_member(self, channel: int, control: str) -> None:
        dsp = self.member_dsp[channel]
        if dsp == NO_DSP:
            return  # cached, applied at the next allocation
        self._send_dsp(dsp, control, self._member_value(channel, control))

    def _send_all_expression(self, dsp: int, channel: int) -> None:
        for control in EXPRESSION_WORDS:
            self._send_dsp(dsp, control, self._member_value(channel, control))

    def _send_dsp(self, dsp: int, control: str, value: int) -> None:
        self._emit(
            "dsp_expression",
            target=dsp,
            channel=self.dsp_owner[dsp],
            control=control,
            value=value,
            command=EXPRESSION_WORDS[control] | value,
        )

    # ------------------------------------------------------------ invariants
    def assert_invariants(self) -> None:
        active_ages = sorted(age for age in self.voice_age if age)
        if active_ages != list(range(1, len(active_ages) + 1)):
            raise AssertionError(f"voice ages are not compact: {self.voice_age}")

        for voice in range(VOICE_COUNT):
            if (self.voice_age[voice] != 0) != (self.voice_note[voice] != NO_NOTE):
                raise AssertionError(f"voice {voice} active/note mismatch")

        if self.enabled:
            for dsp in range(DSP_COUNT):
                owner = self.dsp_owner[dsp]
                if owner == NO_CHANNEL:
                    continue
                if self.member_dsp[owner] != dsp:
                    raise AssertionError(f"dsp {dsp} owner mapping mismatch")
                for voice in self.voices_of(dsp):
                    if self.voice_note[voice] != self.member_note[owner]:
                        raise AssertionError(f"dsp {dsp} voice {voice} note mismatch")
                if not self.pair_mode and self.voice_age[2 * dsp + 1] != 0:
                    raise AssertionError(f"secondary voice of dsp {dsp} active in Single mode")
            for channel in range(CHANNEL_COUNT):
                dsp = self.member_dsp[channel]
                if dsp != NO_DSP and self.dsp_owner[dsp] != channel:
                    raise AssertionError(f"member {channel} dsp mapping mismatch")

        for note, count in enumerate(self.note_hold_count):
            if self.note_present[note] != (count > 0):
                raise AssertionError(f"note presence mismatch for note {note}")
