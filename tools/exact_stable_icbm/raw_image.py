"""Offline raw original/prototype ARM execution. No firmware source substitute.

Peripheral startup, LED and physical transmitter drain are explicit fixtures.
Actual can_rx/can_send/safety/forwarding/queue code runs from supplied bytes.
TXOK acknowledgement is a caller-supplied hardware event, never an ECU model.
"""
import hashlib,json,pathlib,struct
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_HOOK_CODE,UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_PC,UC_ARM_REG_LR,UC_ARM_REG_SP,UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2
HERE=pathlib.Path(__file__).resolve().parent
ABI=json.loads((HERE/'original_abi.json').read_text())
CAN1=0x40006400
TIM2_CNT=0x40000024
PACKET=0x20030000
def packet(address,bus,raw):
  if len(raw)>8:raise ValueError('CAN classic only')
  data=bytearray(16);data[0]=(len(raw)<<4)|(bus<<1)
  struct.pack_into('<I',data,1,(address<<3)|(4 if address>0x7ff else 0))
  data[6:6+len(raw)]=raw
  for b in data[:6+len(raw)]:data[5]^=b
  return bytes(data)

class RawExactImage:
  def __init__(self,image_path,expected_sha256):
    self.image_path=pathlib.Path(image_path)
    raw=self.image_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('raw image hash mismatch')
    if len(raw)>86568 or len(raw)<57540:raise ValueError('outside reviewed image extent')
    self.cpu=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
    self.cpu.mem_map(0x08000000,0x100000);self.cpu.mem_map(0x20000000,0x50000)
    self.cpu.mem_write(0x20000000,b'\xa5'*0x50000);self.cpu.mem_write(0x08004000,raw)
    self.symbols=ABI['aliases'].copy();self.sentinel=0x080F0000
    self.cpu.mem_write(self.sentinel,b'\x00\xbf');self.reached_main=False
    for name in ('__initialize_hardware_early','main'):
      address=self.symbols[name];self.cpu.hook_add(UC_HOOK_CODE,self.on_code,begin=address,end=address)
    self.cpu.mem_map(0x40000000,0x30000)
    self.events=[];self.fifo_releases=0;self.pending_ack=False;self.allow_process=False
    self.hardware_stubs={self.symbols[name]:name for name in ('process_can','process_can.part.0','led_set','diagnostic_puts','diagnostic_puth')}
    for address in self.hardware_stubs:self.cpu.hook_add(UC_HOOK_CODE,self.observe_code,begin=address,end=address)
    for bus in range(3):
      base=CAN1+bus*0x400
      self.cpu.hook_add(UC_HOOK_MEM_WRITE,self.fifo_write,begin=base+12,end=base+15)
      self.cpu.hook_add(UC_HOOK_MEM_READ,self.fifo_read,begin=base+12,end=base+15)
    self.cpu.hook_add(UC_HOOK_MEM_READ|UC_HOOK_MEM_WRITE,self.check_mmio,begin=0x40000000,end=0x4002ffff)
    self.reset()
    for bus in range(3):self.symbols['can_tx%d_q'%(bus+1)]=int.from_bytes(self.read(ABI['queuePointerTable']+bus*4,4),'little')
    if self.call('set_safety_hooks',21,0)!=0:raise AssertionError('PQ21/0 init failed')
    self.last_tick=None

  def on_code(self,cpu,address,size,user):
    if address==self.symbols['__initialize_hardware_early']:cpu.reg_write(UC_ARM_REG_PC,cpu.reg_read(UC_ARM_REG_LR))
    elif address==self.symbols['main']:self.reached_main=True;cpu.emu_stop()

  def observe_code(self,cpu,address,size,user):
    name=self.hardware_stubs.get(address)
    if name and not (self.allow_process and name.startswith('process_can')):
      cpu.reg_write(UC_ARM_REG_PC,cpu.reg_read(UC_ARM_REG_LR))

  def reset(self):
    self.reached_main=False;self.cpu.reg_write(UC_ARM_REG_SP,self.symbols['_estack'])
    self.cpu.emu_start(self.symbols['Reset_Handler']|1,self.sentinel,count=2000000)
    if not self.reached_main:raise AssertionError('startup did not reach original main')

  def call(self,name,*arguments):
    for register,value in zip((UC_ARM_REG_R0,UC_ARM_REG_R1),arguments):self.cpu.reg_write(register,value)
    self.cpu.reg_write(UC_ARM_REG_LR,self.sentinel|1)
    self.cpu.emu_start(self.symbols[name]|1,self.sentinel,count=100000)
    if self.cpu.reg_read(UC_ARM_REG_PC)!=self.sentinel:raise AssertionError(name+' did not return')
    return self.cpu.reg_read(UC_ARM_REG_R0)

  def read(self,address,size):return bytes(self.cpu.mem_read(address,size))
  def write32(self,address,value):self.cpu.mem_write(address,struct.pack('<I',value&0xffffffff))

  def fifo_write(self,cpu,access,address,size,value,user):
    if value&32:self.pending_ack=True;self.fifo_releases+=1

  def state(self):
    return {name:bool(self.read(self.symbols[name],1)[0]) for name in ('controls_allowed','brake_pressed','gas_pressed','vehicle_moving','relay_malfunction','safety_rx_checks_invalid','heartbeat_lost')}

  def fifo_read(self,cpu,access,address,size,value,_):
      if self.pending_ack:
        self.write32(address,0);self.pending_ack=False

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

class PrototypeImage(RawExactImage):
  def __init__(self,manifest_path,image_path=None):
    manifest_path=pathlib.Path(manifest_path)
    self.manifest=json.loads(manifest_path.read_text())
    if self.manifest['originalSha256']!=ABI['originalSha256']:raise ValueError('wrong original basis')
    if image_path is None:image_path=manifest_path.parent/self.manifest['prototypeFile']
    super().__init__(image_path,self.manifest['prototypeSha256'])

def control(image,request,param1=0,param2=0):
  image.cpu.mem_write(PACKET,bytes([request])+struct.pack('<HHH',param1,param2,0))
  return image.call('comms_control_handler',PACKET,PACKET+32)

def status(image):
  if control(image,0xAA)!=8:raise AssertionError('ICBM status length')
  return struct.unpack('<BBBBHH',image.read(PACKET+32,8))

def native(counter,buttons=0):
  value=counter<<20|1<<8|1<<15|1<<18|buttons
  return (value|((value>>8^value>>16^value>>24)&255)).to_bytes(4,'little')
