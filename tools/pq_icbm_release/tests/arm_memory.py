"""Execute actual offline F413 ELF startup and RX ring routines in Unicorn.

Peripheral initialization is explicitly stubbed; this is not a board emulator
and does not prove physical MCU identity, timing, or ECU command acceptance.
"""
from pathlib import Path
import os
import struct
import sys
import unittest
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'panda_icbm_candidate_testdeps'))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_SP, UC_ARM_REG_R0, UC_ARM_REG_R1

class Image:

    def __init__(self, path):
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.cpu.mem_map(134217728, 1048576)
        self.cpu.mem_map(536870912, 327680)
        self.cpu.mem_write(536870912, b'\xa5' * 327680)
        with open(path, 'rb') as source:
            elf = ELFFile(source)
            self.symbols = {s.name: s.entry.st_value for s in elf.get_section_by_name('.symtab').iter_symbols()}
            for segment in elf.iter_segments():
                if segment.header.p_type == 'PT_LOAD' and segment.header.p_filesz:
                    self.cpu.mem_write(segment.header.p_paddr, segment.data())
        self.sentinel = 135200768
        self.cpu.mem_write(self.sentinel, b'\x00\xbf')
        self.reached_main = False
        self.cpu.hook_add(UC_HOOK_CODE, self.on_code)

    def on_code(self, cpu, address, size, user_data):
        if address == self.symbols['__initialize_hardware_early'] & ~1:
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))
        elif address == self.symbols['main'] & ~1:
            self.reached_main = True
            cpu.emu_stop()

    def reset(self):
        self.reached_main = False
        self.cpu.reg_write(UC_ARM_REG_SP, self.symbols['_estack'])
        self.cpu.emu_start(self.symbols['Reset_Handler'] | 1, self.sentinel, count=2000000)
        if not self.reached_main:
            raise AssertionError('startup did not reach main')

    def call(self, name, *arguments):
        for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1), arguments):
            self.cpu.reg_write(register, value)
        self.cpu.reg_write(UC_ARM_REG_LR, self.sentinel | 1)
        self.cpu.emu_start(self.symbols[name] | 1, self.sentinel, count=100000)
        if self.cpu.reg_read(UC_ARM_REG_PC) != self.sentinel:
            raise AssertionError(f'{name} failed to return')
        return self.cpu.reg_read(UC_ARM_REG_R0)

    def read(self, start, size):
        return bytes(self.cpu.mem_read(start, size))
