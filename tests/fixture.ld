ENTRY(entry)
SECTIONS {
  . = 0x80000400;
  .text : { *(.text) }
  .data : { *(.data) }
  /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) *(.pdr) *(.gnu.attributes) }
}
