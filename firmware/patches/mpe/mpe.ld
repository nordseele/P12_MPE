/* Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md. */
/* Link the MPE patch into the free flash after the linked application image
 * (research/memory_placement.md). Only .text and .rodata are allowed; any
 * writable section is a build error, checked by tools/make_mpe_image.py. */

ENTRY(mpe_midi_submit_event)

/* Stock firmware, Main OS 1.4.2.3 */
SendCommandToDsp            = 0x9d0052f4;
SendCommandToLayerDsps      = 0x9d0053a8;
BroadcastDspCommand         = 0x9d00524c;
ActivateVoiceForNote        = 0x9d01be30;
ReleaseVoiceAndCompactAges  = 0x9d01c6c8;
GetGlobalParameter          = 0x9d005f30;
GetLayerParameter           = 0x9d005f4c;
GetSelectedLayerState       = 0x9d01a05c;
LoadGlobalsFromSpiFlash     = 0x9d00435c;
SpiFlashProgram256          = 0x9d003a4c;
SpiReadByte                 = 0x9d00388c;
GetMenuCursor               = 0x9d019fb0;
GetMenuTopRow               = 0x9d019fe0;
PostPageEvent               = 0x9d02128c;
DrawTextInBox               = 0x9d02ab70;
TextWidth                   = 0x9d02f3e8;
TrackAndAllocateNote        = 0x9d01c2ec;
TrackAndReleaseNote         = 0x9d01c9dc;
ArpTick                     = 0x9d0348d0;
MainLoopHelper              = 0x9d01a074;
MidiReadDataByte            = 0x9d02b3d8;

/* Continuation points after the two relocated entry instructions */
MidiSubmitEvent_cont            = 0x9d01a3f8;
AllocateVoiceForNote_cont       = 0x9d01c07c;
ReleaseVoicesMatchingNote_cont  = 0x9d01c804;
MidiIsChannelAccepted_cont      = 0x9d02c3fc;
SendProgramParametersToDsps_cont = 0x9d02b074;
GlobalMenuHandler_cont      = 0x9d00a4f0;
GlobalMenuDisplay_cont      = 0x9d028a64;
ArpListAddNote_cont         = 0x9d033ccc;
ArpListRemoveNote_cont      = 0x9d033e68;

/* UI debug option: event producers and display (gp = 0xa00086f0) */
UiButton_cont   = 0x9d01a254;
UiEncoder_cont  = 0x9d01a2a0;
UiPot_cont      = 0x9d01a2f4;
SetLayerParameter_cont  = 0x9d0067d0;
SetGlobalParameter_cont = 0x9d007590;
DinMidiTxEnqueue = 0x9d02b4a4;
SetFont         = 0x9d02edbc;
DrawTextAt      = 0x9d02f2dc;
stock_font      = 0x9d07003c;
draw_colour     = 0xa00008ec;
current_font    = 0xa000ded8;
frame_buffer    = 0xa0003880;
ui_debug_state  = 0xa0014100;

/* RAM */
voice_age   = 0xa0009a90;
voice_note  = 0xa0009ac0;
spi_page_buffer = 0xa0007e00;
mpe_state   = 0xa0014000;   /* above the 1 KiB heap [0xa0012660, 0xa0012a60) */
arp_mpe_state = 0xa0014400; /* 0xa0014400-0xa00147ff reserved for the arp */
arp_note_list = 0xa0000e38; /* stock arp list, u16[2][32] */
arp_count   = 0xa00007e4;   /* gp-0x7f0c, u16[2] */
arp_index   = 0xa00007e8;   /* gp-0x7f08, u16[2] */
/* Stock MIDI parser state (research/mpe_manager_and_mcm.md) */
din_cc_number   = 0xa0009a86;   /* event record d1 */
din_channel     = 0xa00008e1;   /* gp-0x7e0f */
din_nrpn_number = 0xa00007aa;   /* gp-0x7f46 */
usb_nrpn_number = 0xa000080e;   /* gp-0x7ee2 */
usb_rx_index    = 0xa00009b2;   /* gp-0x7d3e */
usb_rx_packets  = 0xa00104a0;

SECTIONS
{
    . = 0x9d073460;
    .text : {
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    }
    .data : { *(.data*) *(.sdata*) }
    .bss : { *(.bss*) *(.sbss*) *(COMMON) }
    /DISCARD/ : {
        *(.comment) *(.MIPS.*) *(.gnu.*) *(.note*) *(.reginfo) *(.pdr)
        *(.gnu.attributes) *(.mdebug*)
    }
}
