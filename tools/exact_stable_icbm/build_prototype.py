"""Hash-gated UNSIGNED exact-stable extension; no signing or device access."""
import argparse,hashlib,json,pathlib,struct,subprocess
from elftools.elf.elffile import ELFFile
HERE=pathlib.Path(__file__).resolve().parent
STABLE='3b9179406c4a43319af3383157fc83e1098cba4d'
BASE,APPEND=0x08004000,0x080120D0
SHA='ef8ea87c17f0802bec38f754d93a3fb2c9850c640b5ba95e01e661323545daba'

def bl(site,target):
  target&=~1;offset=target-(site+4)
  if offset&1 or not -(1<<24)<=offset<(1<<24):raise ValueError('BL range/alignment')
  value=offset&0x1ffffff;s=(value>>24)&1;i1=(value>>23)&1;i2=(value>>22)&1
  return struct.pack('<HH',0xf000|(s<<10)|((value>>12)&0x3ff),0xd000|((1^(i1^s))<<13)|((1^(i2^s))<<11)|((value>>1)&0x7ff))

def build(signed,module_path,output_dir):
  if hashlib.sha256(signed).hexdigest()!=SHA:raise ValueError('original hash mismatch')
  original=signed[:-128]
  if len(original)!=57540 or original[-8:]!=b'VERS\x02\x00\x00\x00':raise ValueError('original framing mismatch')
  with module_path.open('rb') as stream:
    elf=ELFFile(stream)
    symbols={s.name:int(s.entry.st_value) for s in elf.get_section_by_name('.symtab').iter_symbols()}
    text=elf.get_section_by_name('.text')
    if text.header.sh_addr!=APPEND:raise ValueError('module address mismatch')
    payload=text.data()
  if payload!=module_path.with_suffix('.bin').read_bytes():raise ValueError('ELF/bin mismatch')
  if APPEND+len(payload)>0x080191A0 or symbols['_extension_bss']!=0x20040000 or symbols['_extension_bss_end']-symbols['_extension_bss']>1024:
    raise ValueError('placement budget')
  result=bytearray(original);hunks=[]
  def patch(site,expected,replacement,label):
    offset=site-BASE
    if result[offset:offset+len(expected)]!=expected or len(expected)!=len(replacement):raise ValueError('hook preimage '+label)
    result[offset:offset+len(expected)]=replacement
    hunks.append({'label':label,'address':hex(site),'offset':offset,'original':expected.hex(),'replacement':replacement.hex()})
  tbh=(symbols['exact_icbm_usb_veneer']&~1)-0x0800B24C
  if tbh<0 or tbh&1 or tbh//2>65535:raise ValueError('TBH range')
  for site,label in ((0x0800B24E,'USB A9'),(0x0800B250,'USB AA')):
    patch(site,b'\x82\x02',struct.pack('<H',tbh//2),label)
  for site,old,new,label in ((0x0800AE22,0x0800AC40,'exact_icbm_forward','native GRA forwarding'),
                             (0x0800AB68,0x0800A6E4,'exact_icbm_checksum_observe','TXOK checksum and observer'),
                             (0x0800BFA6,0x0800BFC8,'exact_icbm_startup_veneer','extension init then original main')):
    patch(site,bl(site,old),bl(site,symbols[new]),label)
  allowed={h['offset']+i for h in hunks for i in range(len(bytes.fromhex(h['original'])))}
  changed=[i for i,(a,b) in enumerate(zip(original,result)) if a!=b]
  if not set(changed)<=allowed:raise ValueError('non-allowlisted original change')
  if result[-8:]!=original[-8:]:raise ValueError('old version tag changed')
  result.extend(bytes(APPEND-BASE-len(result)));result.extend(payload)
  output_dir.mkdir(parents=True,exist_ok=True)
  output=output_dir/'exact-stable-icbm.prototype.unsigned.bin'
  if output.exists():raise ValueError('prototype output must be new')
  output.write_bytes(result)
  report={'status':'UNSIGNED_PROTOTYPE_NOT_BOOTSTUB_FRAMED','originalSha256':SHA,'originalUnsignedSha256':hashlib.sha256(original).hexdigest(),
    'originalBytes':len(original),'prototypeFile':output.name,'prototypeBytes':len(result),'prototypeSha256':hashlib.sha256(result).hexdigest(),
    'originalChangedByteCount':len(changed),'allowlistedOriginalSpanBytes':len(allowed),'hunks':hunks,
    'allOtherOriginalBytesIdentical':True,'oldVersionTagPreserved':True,'originalLengthWordUnchanged':True,
    'moduleSha256':hashlib.sha256(payload).hexdigest(),'elfSha256':hashlib.sha256(module_path.read_bytes()).hexdigest(),
    'extensionStart':hex(APPEND),'extensionEnd':hex(APPEND+len(payload)),'extensionStateStart':hex(symbols['_extension_bss']),
    'extensionStateBytes':symbols['_extension_bss_end']-symbols['_extension_bss'],
    'entryPoints':{name:hex(symbols[name]) for name in ('exact_icbm_usb_veneer','exact_icbm_startup_veneer','exact_icbm_init','exact_icbm_forward','exact_icbm_checksum_observe','exact_icbm_control')},
    'signing':'NOT performed; future authorized signing must frame SETLEN/new final VERS2 and signature; this file is not installable'}
  (output_dir/'prototype-manifest.json').write_text(json.dumps(report,indent=2))
  print(json.dumps(report))

if __name__=='__main__':
  parser=argparse.ArgumentParser(description=__doc__)
  inputs=parser.add_mutually_exclusive_group(required=True)
  inputs.add_argument('--original',type=pathlib.Path)
  inputs.add_argument('--repo',type=pathlib.Path,help='Extract only the pinned public stable Git firmware blob')
  parser.add_argument('--module-elf',type=pathlib.Path,default=HERE/'build/module.elf')
  parser.add_argument('--output',type=pathlib.Path,required=True)
  args=parser.parse_args()
  if args.original:signed=args.original.read_bytes()
  else:signed=subprocess.check_output(['git','-c','safe.directory='+str(args.repo.resolve()),'-C',str(args.repo),'show',STABLE+':panda/board/obj/panda.bin.signed'])
  build(signed,args.module_elf,args.output)
