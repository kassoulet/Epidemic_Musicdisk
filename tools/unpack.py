import struct,sys
from unicorn import *
from unicorn.x86_const import *
src=sys.argv[1]
d=open(src,'rb').read()
cblp,cp,crlc,hdr,minalloc,maxalloc,ss,sp,_,ip,cs,lfarlc=struct.unpack('<12H',d[2:26])
size=(cp-1)*512+cblp if cblp else cp*512
img=d[hdr*16:size]
BASE=0x1000  # load segment
mu=Uc(UC_ARCH_X86,UC_MODE_16)
mu.mem_map(0,0x100000)
mu.mem_write(BASE*16,img)
for i in range(crlc):
    o,s=struct.unpack('<2H',d[lfarlc+4*i:lfarlc+4*i+4])
    a=(BASE+s)*16+o
    v=struct.unpack('<H',mu.mem_read(a,2))[0]
    mu.mem_write(a,struct.pack('<H',(v+BASE)&0xffff))
mu.reg_write(UC_X86_REG_CS,BASE+cs);mu.reg_write(UC_X86_REG_IP,ip)
mu.reg_write(UC_X86_REG_SS,BASE+ss);mu.reg_write(UC_X86_REG_SP,sp)
mu.reg_write(UC_X86_REG_DS,BASE-16);mu.reg_write(UC_X86_REG_ES,BASE-16)
lim=BASE*16+int(sys.argv[2],16)*16 if len(sys.argv)>2 else None
done=[]
seen=[0]
def hook(uc,addr,sz,_):
    if addr>=lim: seen[0]=1
    if seen[0] and addr<lim: 
        done.append(addr);uc.emu_stop()
mu.hook_add(UC_HOOK_CODE,hook)
try: mu.emu_start((BASE+cs)*16+ip,0,count=200000000)
except UcError as e: print('err',e,hex(mu.reg_read(UC_X86_REG_CS)),hex(mu.reg_read(UC_X86_REG_IP)))
print('stopped',[hex(x) for x in done], 'cs:ip',hex(mu.reg_read(UC_X86_REG_CS)-BASE),hex(mu.reg_read(UC_X86_REG_IP)))
for r in ('SS','SP','DS','ES'): print(r,hex(mu.reg_read(getattr(__import__('unicorn.x86_const',fromlist=['x']),'UC_X86_REG_'+r))))
open('mem.bin','wb').write(bytes(mu.mem_read(BASE*16,0x80000)))
