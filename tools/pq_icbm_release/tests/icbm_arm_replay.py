"""Generic production ARM RX/forwarding/TX queue and TXOK fixture.

Imports existing repository arm_can/arm_memory helpers; caller configures source.
No ECU behavior or physical acceptance is implied.
"""
import collections, hashlib, json, pathlib, struct, sys
from arm_can import CANImage, PACKET, TIM2_CNT, CAN1
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_HOOK_CODE,UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
from elftools.elf.elffile import ELFFile
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC,UC_ARM_REG_XPSR


def packet(address,bus,raw):
  if len(raw)>8:raise ValueError('CAN classic only')
  data=bytearray(16);data[0]=(len(raw)<<4)|(bus<<1)
  struct.pack_into('<I',data,1,(address<<3)|(4 if address>0x7ff else 0))
  data[6:6+len(raw)]=raw
  for b in data[:6+len(raw)]:data[5]^=b
  return bytes(data)

class ReplayImage(CANImage):
  def __init__(self,path):
    # Same loader/startup as Image, but range-bound code hooks avoid a Python
    # callback for every ARM instruction. Policy code remains unmodified.
    self.cpu=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
    self.cpu.mem_map(0x08000000,0x100000)
    self.cpu.mem_map(0x20000000,0x50000)
    self.cpu.mem_write(0x20000000,b'\xa5'*0x50000)
    with open(path,'rb') as source:
      elf=ELFFile(source)
      self.symbols={s.name:s.entry.st_value for s in elf.get_section_by_name('.symtab').iter_symbols()}
      for segment in elf.iter_segments():
        if segment.header.p_type=='PT_LOAD' and segment.header.p_filesz:self.cpu.mem_write(segment.header.p_paddr,segment.data())
    self.sentinel=135200768;self.cpu.mem_write(self.sentinel,b'\x00\xbf');self.reached_main=False
    for name in ('__initialize_hardware_early','main'):
      address=self.symbols[name]&~1
      self.cpu.hook_add(UC_HOOK_CODE,self.on_code,begin=address,end=address)
    self.cpu.mem_map(0x40000000,0x30000)
    self.events=[];self.fifo_releases=0;self.pending_ack=False;self.allow_process=False
    self.hardware_stubs={self.symbols[name]&~1:name for name in ('process_can','process_can.part.0','led_set','led_set.part.0') if name in self.symbols}
    self.observers={}
    for address in self.hardware_stubs:
      self.cpu.hook_add(UC_HOOK_CODE,self.observe_code,begin=address,end=address)
    self.cpu.hook_add(UC_HOOK_MEM_WRITE,self.fifo_write,begin=CAN1+12,end=CAN1+15)
    self.cpu.hook_add(UC_HOOK_MEM_READ,self.fifo_read,begin=CAN1+12,end=CAN1+15)
    self.cpu.hook_add(UC_HOOK_MEM_READ|UC_HOOK_MEM_WRITE,self.check_mmio,begin=0x40000000,end=0x4002ffff)
    self.write32(537001980,3735925009);self.reset();self.call('set_safety_hooks',21,0)
    self.guards(controls=False)
    for base in (CAN1+0x400,CAN1+0x800):
      self.cpu.hook_add(UC_HOOK_MEM_WRITE,self.fifo_write,begin=base+12,end=base+15)
      self.cpu.hook_add(UC_HOOK_MEM_READ,self.fifo_read,begin=base+12,end=base+15)
    self.last_tick=None

  def fifo_read(self,cpu,access,address,size,value,_):
    if self.pending_ack:
      self.write32(address,0);self.pending_ack=False

  def observe_code(self,cpu,address,size,user):
    if self.allow_process and self.hardware_stubs.get(address,'').startswith('process_can'):
      return
    super().observe_code(cpu,address,size,user)

  def check_mmio(self,cpu,access,address,size,value,_):
    allowed=[(TIM2_CNT,TIM2_CNT+4)]
    for base in (CAN1,CAN1+0x400,CAN1+0x800):
      allowed.extend(((base+8,base+16),(base+384,base+400),(base+432,base+448)))
    if not any(start<=address and address+size<=end for start,end in allowed):
      raise AssertionError('unmodeled MMIO %x'%address)

  def clock(self,ns,start):
    elapsed=(ns-start)//1000+1000
    self.write32(TIM2_CNT,elapsed&0xffffffff)
    second=elapsed//1000000
    if self.last_tick is None:self.last_tick=second
    if second>self.last_tick:
      self.write32(self.symbols['safety_mode_cnt'],second)
      self.call('safety_tick',self.symbols['current_safety_config'])
      self.last_tick=second

  def queues(self):
    return {str(bus):self.drain('can_tx%d_q'%(bus+1)) for bus in range(3)},self.drain('can_rx_q')

  def drain(self,queue):
    # Observe empty queue without entering the ARM no-op critical section.
    # Every nonempty pop still executes production can_pop; no indices changed
    # by the fixture. This avoids four empty emu_start calls per CAN frame.
    address=self.symbols[queue];output=[]
    while True:
      w_ptr,r_ptr=struct.unpack('<II',self.read(address,8))
      if w_ptr==r_ptr:return output
      if not self.call('can_pop',address,PACKET+32):raise AssertionError('queue observation changed')
      output.append(self.read(PACKET+32,16))

  def rx(self,address,bus,raw):
    base=CAN1+bus*0x400
    self.events=[];before=self.fifo_releases
    self.write32(base+12,1)
    rir=(address<<3)|4 if address>0x7ff else address<<21
    self.cpu.mem_write(base+432,struct.pack('<IIII',rir,len(raw),int.from_bytes(raw[:4].ljust(4,b'\x00'),'little'),int.from_bytes(raw[4:].ljust(4,b'\x00'),'little')))
    self.call('can_rx',bus)
    if self.fifo_releases!=before+1:raise AssertionError('FIFO ack')
    return self.queues()

  def tx(self,address,bus,raw):
    self.events=[]
    self.cpu.mem_write(PACKET,packet(address,bus,raw))
    self.cpu.reg_write(UC_ARM_REG_R2,0)
    self.call('can_send',PACKET,bus)
    return self.queues()

  def ack_return(self,queued_packet,target_bus):
    """Explicit synthetic TXOK mailbox fixture; production process_can runs.

    Caller must pass an actually queued frame and acknowledge each at most once.
    This models successful physical transmission, never radar acknowledgement.
    """
    packed=struct.unpack_from('<I',queued_packet,1)[0]
    address=packed>>3;extended=bool(packed&4);length=queued_packet[0]>>4
    payload=queued_packet[6:6+length]
    base=CAN1+target_bus*0x400
    tir=(address<<3)|4 if extended else address<<21
    self.cpu.mem_write(base+384,struct.pack('<IIII',tir,length,int.from_bytes(payload[:4].ljust(4,b'\x00'),'little'),int.from_bytes(payload[4:].ljust(4,b'\x00'),'little')))
    self.write32(base+8,(1<<26)|3)
    self.allow_process=True
    try:self.call('process_can',target_bus)
    finally:
      self.allow_process=False;self.write32(base+8,0)
    return self.drain('can_rx_q')

  def state(self):
    return {name:bool(self.read(self.symbols[name],1)[0]) for name in
      ('controls_allowed','brake_pressed','gas_pressed','vehicle_moving','relay_malfunction','safety_rx_checks_invalid','heartbeat_lost')}
