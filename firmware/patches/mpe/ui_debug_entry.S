/*
 * Relocated entries of the three front-panel event producers (UI debug
 * option). Same convention as mpe_entry.S; verified by tools/make_mpe_image.py.
 */
    .set noreorder
    .set noat
    .text

    .globl orig_ui_button
orig_ui_button:                    /* FUN_9d01a24c, buttons */
    addiu   $sp, $sp, -0x20
    sw      $s1, 0x14($sp)
    j       UiButton_cont          /* 0x9d01a254 */
    nop

    .globl orig_ui_encoder
orig_ui_encoder:                   /* FUN_9d01a298, encoders */
    addiu   $sp, $sp, -0x20
    lui     $a2, 0x6
    j       UiEncoder_cont         /* 0x9d01a2a0 */
    nop

    .globl orig_ui_pot
orig_ui_pot:                       /* FUN_9d01a2ec, pots, sliders, wheels */
    addiu   $sp, $sp, -0x20
    sw      $s2, 0x18($sp)
    j       UiPot_cont             /* 0x9d01a2f4 */
    nop

    .globl orig_set_layer_parameter
orig_set_layer_parameter:          /* SetLayerParameterAndRoute 0x9d0067c8 */
    addiu   $sp, $sp, -0x40
    sw      $s1, 0x1c($sp)
    j       SetLayerParameter_cont /* 0x9d0067d0 */
    nop

    .globl orig_set_global_parameter
orig_set_global_parameter:         /* SetGlobalParameter 0x9d007588 */
    addiu   $sp, $sp, -0x20
    sw      $s1, 0x14($sp)
    j       SetGlobalParameter_cont /* 0x9d007590 */
    nop
