OUTPUT_FORMAT("elf32-littlearm")
OUTPUT_ARCH(arm)
ENTRY(exact_icbm_startup_veneer)
original_main = 0x0800BFC9;
SECTIONS {
  .text 0x080120D0 : { KEEP(*(.text.veneers)) *(.text*) *(.rodata*) }
  _extension_flash_end = .;
  .bss 0x20040000 (NOLOAD) : { _extension_bss = .; *(.bss*) *(COMMON) _extension_bss_end = .; }
  /DISCARD/ : { *(.comment*) *(.ARM.exidx*) *(.ARM.extab*) *(.note*) }
}
ASSERT(_extension_flash_end <= 0x080191A0,"extension exceeds observed live flash extent")
ASSERT(_extension_bss_end - _extension_bss <= 1024,"extension state exceeds reserved budget")
