/* Replaces "lw a0,0(s3)" at 0x9d005e30 in the DSP boot sender loop with
 * "jal dsp_stream_fetch"; the delay slot (clear v1) is unchanged. Returns
 * word s2 of the rebuilt stream in a0 and keeps v1 = 0 for the loop. */
    .set noreorder
    .text
    .globl dsp_stream_fetch
dsp_stream_fetch:
    addiu   $sp, $sp, -24
    sw      $ra, 20($sp)
    jal     dsp_stream_word
    move    $a0, $s2
    move    $a0, $v0
    lw      $ra, 20($sp)
    move    $v1, $zero
    jr      $ra
    addiu   $sp, $sp, 24
