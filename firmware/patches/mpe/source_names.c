/* MPE names for three mod-matrix sources (--xyz-source-names). The stock
 * source-name pointer table at 0x9d06d4b4 (one pointer per DSP source ID,
 * read by 0x9d01afac) is redirected to these strings for Pitchbend (0xd),
 * Aftertouch (0x13) and CC#2: Breath (0x14). Names only: the DSP sources are
 * unchanged (research/sharc_voice_structure.md). */

const char mpe_source_name_x[] = "X: Pitchbend";
const char mpe_source_name_z[] = "Z: Pressure";
const char mpe_source_name_y[] = "Y: Timbre";
