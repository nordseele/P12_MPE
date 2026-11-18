/* Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md. */
/*
 * Relocated entries of the hooked functions.
 *
 * Each hooked entry E is overwritten with "j <wrapper>; nop". The two
 * original instructions move here, followed by a jump back to E + 8.
 * None of the moved instructions is PC-relative, and no branch targets
 * E + 4 (checked in Ghidra). tools/make_mpe_image.py verifies that these
 * words equal the original image words.
 */
    .set noreorder
    .set noat
    .text

    .globl orig_midi_submit_event
orig_midi_submit_event:            /* MidiSubmitEvent 0x9d01a3f0 */
    lui     $t1, 0xa001
    addiu   $v0, $t1, -0x6580
    j       MidiSubmitEvent_cont   /* 0x9d01a3f8 */
    nop

    .globl orig_allocate_voice
orig_allocate_voice:               /* AllocateVoiceForNote 0x9d01c074 */
    addiu   $sp, $sp, -0x30
    sw      $s1, 0x14($sp)
    j       AllocateVoiceForNote_cont  /* 0x9d01c07c */
    nop

    .globl orig_release_voices
orig_release_voices:               /* ReleaseVoicesMatchingNote 0x9d01c7fc */
    addiu   $sp, $sp, -0x30
    sw      $s3, 0x1c($sp)
    j       ReleaseVoicesMatchingNote_cont  /* 0x9d01c804 */
    nop

    .globl orig_send_program_parameters
orig_send_program_parameters:      /* SendProgramParametersToDsps 0x9d02b06c */
    addiu   $sp, $sp, -0x18
    addu    $a0, $zero, $zero      /* clear a0 */
    j       SendProgramParametersToDsps_cont  /* 0x9d02b074 */
    nop

    .globl orig_channel_accepted
orig_channel_accepted:             /* MidiIsChannelAccepted 0x9d02c3f4 */
    addiu   $sp, $sp, -0x20
    sw      $s0, 0x10($sp)
    j       MidiIsChannelAccepted_cont  /* 0x9d02c3fc */
    nop

    .globl orig_global_menu_handler
orig_global_menu_handler:          /* GlobalMenuStateHandler 0x9d00a4e8 */
    addiu   $sp, $sp, -0x48
    sw      $s3, 0x2c($sp)
    j       GlobalMenuHandler_cont /* 0x9d00a4f0 */
    nop

    .globl orig_global_menu_display
orig_global_menu_display:          /* Global menu page drawing 0x9d028a5c */
    addiu   $sp, $sp, -0x90
    sw      $s3, 0x74($sp)
    j       GlobalMenuDisplay_cont /* 0x9d028a64 */
    nop

    .globl orig_arp_list_add
orig_arp_list_add:                 /* ArpListAddNote 0x9d033cc4 */
    addiu   $sp, $sp, -0x20
    andi    $a0, $a0, 0xff
    j       ArpListAddNote_cont    /* 0x9d033ccc */
    nop

    .globl orig_arp_list_remove
orig_arp_list_remove:              /* ArpListRemoveNote 0x9d033e60 */
    addiu   $sp, $sp, -0x28
    andi    $a0, $a0, 0xff
    j       ArpListRemoveNote_cont /* 0x9d033e68 */
    nop
