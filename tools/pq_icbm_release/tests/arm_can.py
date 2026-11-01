"""Bounded execution of the actual ARM ICBM adapter, CAN RX and C safety.

Run: python test_arm_icbm.py
ICBM_TEST_ELF selects another ELF (default: artifacts/matrix/icbm.elf).
Only peripheral initialization (Image), LED I/O, TX hardware draining, and a
single bxCAN FIFO release side effect are stubbed. Policy, adapter, packet
checksums, RX/TX hooks, and queues execute from the ELF without replacement.
Safety/heartbeat observations are explicit fixture inputs, not a whole-vehicle
or timing model. No device, signer, keys, or firmware source changes are used.
"""
import csv
import hashlib
import os
from pathlib import Path
import struct
import unittest
from arm_memory import Image, ROOT
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2
ELF = Path(os.environ.get('ICBM_TEST_ELF', str(ROOT / 'artifacts/matrix/icbm.elf')))
FIXTURE = ROOT.parent / 'reference_transform/native_prototype/true3dc_reference_port/fixture.csv'
MASK32 = 4294967295
CAN1 = 1073767424
TIM2_CNT = 1073741860
PACKET, POPPED = (537067520, 537067552)
DRIVER_MASK = 31 << 9 | 3 << 16 | 15 << 24

def packet(raw, bus=0, addr=906):
    """F4 packed/aligned CANPacket_t: six header bytes, eight data, two pad."""
    data = bytearray(bytes([64 | bus << 1]) + struct.pack('<I', addr << 3) + b'\x00')
    data += struct.pack('<I', raw) + bytes(6)
    checksum = 0
    for byte in data[:10]:
        checksum ^= byte
    data[5] = checksum
    return bytes(data)

def raw_packet(data):
    return struct.unpack_from('<I', data, 6)[0]

def synthetic(counter, buttons=0):
    raw = counter << 20 | 1 << 8 | 1 << 15 | 1 << 18 | buttons
    return raw | (raw >> 8 ^ raw >> 16 ^ raw >> 24) & 255

class CANImage(Image):

    def __init__(self, path):
        super().__init__(path)
        self.cpu.mem_map(1073741824, 196608)
        self.events = []
        self.fifo_releases = 0
        self.pending_ack = False
        self.hardware_stubs = {self.symbols[name] & ~1: name for name in ('process_can', 'process_can.part.0', 'led_set', 'led_set.part.0') if name in self.symbols}
        self.observers = {self.symbols[name] & ~1: name for name in ('safety_tx_hook', 'safety_rx_hook', 'can_push')}
        self.cpu.hook_add(UC_HOOK_CODE, self.observe_code)
        self.cpu.hook_add(UC_HOOK_MEM_WRITE, self.fifo_write, begin=CAN1 + 12, end=CAN1 + 15)
        self.cpu.hook_add(UC_HOOK_MEM_READ, self.fifo_read, begin=CAN1 + 12, end=CAN1 + 15)
        self.cpu.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.check_mmio, begin=1073741824, end=1073938431)
        self.write32(537001980, 3735925009)
        self.reset()
        self.call('set_safety_hooks', 21, 0)
        if self.read(self.symbols['current_safety_mode'], 2) != b'\x15\x00':
            raise AssertionError('actual safety initialization did not select VW PQ')
        if self.symbols['_ebss'] > PACKET or POPPED + 16 >= 537133056:
            raise AssertionError('scratch range overlaps static objects')
        self.guards()

    def write32(self, address, value):
        self.cpu.mem_write(address, struct.pack('<I', value & MASK32))

    def boolean(self, name, value):
        self.cpu.mem_write(self.symbols[name], bytes([bool(value)]))

    def guards(self, controls=True, brake=False, gas=False, relay_ok=True, heartbeat=True, valid=True):
        for name, value in {'controls_allowed': controls, 'brake_pressed': brake, 'brake_pressed_prev': brake, 'gas_pressed': gas, 'gas_pressed_prev': gas, 'relay_malfunction': not relay_ok, 'heartbeat_lost': not heartbeat, 'safety_rx_checks_invalid': not valid}.items():
            self.boolean(name, value)

    def observe_code(self, cpu, address, size, _):
        if address in self.hardware_stubs:
            self.events.append((self.hardware_stubs[address],))
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))
        elif address in self.observers:
            name = self.observers[address]
            argument = cpu.reg_read(UC_ARM_REG_R0)
            data = self.read(cpu.reg_read(UC_ARM_REG_R1) if name == 'can_push' else argument, 16)
            self.events.append((name, argument, data))

    def fifo_write(self, cpu, access, address, size, value, _):
        if value & 32:
            self.pending_ack = True
            self.fifo_releases += 1

    def fifo_read(self, cpu, access, address, size, value, _):
        if self.pending_ack:
            self.write32(CAN1 + 12, 0)
            self.pending_ack = False

    def check_mmio(self, cpu, access, address, size, value, _):
        allowed = ((CAN1 + 12, CAN1 + 16), (CAN1 + 432, CAN1 + 448), (TIM2_CNT, TIM2_CNT + 4))
        if not any((start <= address and address + size <= end for start, end in allowed)):
            raise AssertionError(f'unmodeled MMIO access: {address:#x}, size={size}')

    def drain(self, queue):
        output = []
        while self.call('can_pop', self.symbols[queue], POPPED):
            output.append(self.read(POPPED, 16))
        return output

    def receive(self, raw, now):
        self.events = []
        self.write32(TIM2_CNT, now)
        self.write32(CAN1 + 12, 1)
        self.cpu.mem_write(CAN1 + 432, struct.pack('<IIII', 906 << 21, 4, raw, 0))
        releases = self.fifo_releases
        self.call('can_rx', 0)
        if self.fifo_releases != releases + 1:
            raise AssertionError('CAN RX must acknowledge exactly one FIFO entry')
        events = list(self.events)
        return (self.drain('can_tx3_q'), self.drain('can_rx_q'), events)

    def host(self, raw, now):
        self.events = []
        self.write32(TIM2_CNT, now)
        self.cpu.mem_write(PACKET, packet(raw, bus=2))
        self.cpu.reg_write(UC_ARM_REG_R2, 0)
        self.call('can_send', PACKET, 2)
        events = list(self.events)
        return (self.drain('can_tx3_q'), self.drain('can_rx_q'), events)

    def policy(self, raw, bus=2):
        self.cpu.mem_write(PACKET, packet(raw, bus))
        return self.call('safety_tx_hook', PACKET)
