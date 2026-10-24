"""Run the unpacked 32-bit Epidemic program under unicorn with a minimal DOS32 + VGA(mode X) + PIT/keyboard model.
usage: emu.py EPIDEMIC.EXE seg32.bin outdir 'tick:action,...'   actions: shot | key:NAME | stop
seg32.bin = unpacked image from tools/unpack.py (mem.bin[0x2630:])."""
import struct, sys, os
from unicorn import *
from unicorn.x86_const import *

exe_path, seg_path, outdir, script = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else ''
EXE = open(exe_path, 'rb').read()
os.makedirs(outdir, exist_ok=True)
SEG = open(seg_path, 'rb').read()
IVT = 0xF00000          # virtual real-mode IVT (gs base)
STUB_GET, STUB_SET = 0xF80000, 0xF80010
MB = 1 << 20

mu = Uc(UC_ARCH_X86, UC_MODE_32)
mu.mem_map(0, 0xA0000)
mu.mem_map(0xB0000, 0x1000000 - 0xB0000)

# ---------------- VGA model ----------------
class VGA:
    def __init__(s):
        s.planes = [bytearray(65536) for _ in range(4)]
        s.seq = [0] * 8; s.seq[2] = 0xF; s.seq[4] = 0x0E
        s.crtc = [0] * 0x20; s.gc = [0] * 9; s.gc[8] = 0xFF
        s.seqi = s.crtci = s.gci = 0
        s.dac = [0] * 768; s.dacw = 0; s.dacc = 0
        s.latch = [0] * 4; s.vs = 0; s.attr_flip = 0; s.attr = [0] * 32
    def chain4(s): return bool(s.seq[4] & 8)
    trace = None
    def write(s, off, val):  # 8-bit write at A0000+off
        if s.trace is not None:
            for p in range(4):
                if s.seq[2] >> p & 1: s.trace[(off % 80 * 4 + p, off // 80)] = val
        if s.chain4():
            s.planes[off & 3][off >> 2] = val; return
        wm = s.gc[5] & 3
        if wm == 1:
            for p in range(4):
                if s.seq[2] >> p & 1: s.planes[p][off] = s.latch[p]
            return
        for p in range(4):
            if s.seq[2] >> p & 1: s.planes[p][off] = val
    def read(s, off):
        if s.chain4(): return s.planes[off & 3][off >> 2]
        for p in range(4): s.latch[p] = s.planes[p][off]
        return s.latch[s.gc[4] & 3]
    def frame(s):
        start = (s.crtc[0xC] << 8) | s.crtc[0xD]; stride = s.crtc[0x13] * 2 or 80
        img = bytearray(320 * 400)
        for y in range(400):
            row = start + y * stride
            for x in range(320):
                img[y * 320 + x] = s.planes[x & 3][(row + (x >> 2)) & 0xFFFF]
        from PIL import Image
        im = Image.frombytes('P', (320, 400), bytes(img))
        im.putpalette([min(255, v * 4 + (v >> 4)) for v in s.dac]); return im
vga = VGA()
def vga_r(uc, off, size, ud):
    v = 0
    for i in range(size): v |= vga.read(off + i) << (8 * i)
    return v
def vga_w(uc, off, size, val, ud):
    for i in range(size): vga.write(off + i, val >> (8 * i) & 255)
mu.mmio_map(0xA0000, 0x10000, vga_r, None, vga_w, None)

# ---------------- ports ----------------
kbd = [0]; tick = [0]; st3da = [0]
def port_in(uc, port, size, ud):
    if port == 0x3DA: st3da[0] += 1; return 8 if (st3da[0] // 3) & 1 else 1
    if port == 0x60: return kbd[0]
    if port == 0x246: return 0xC   # GUS IRQ status: timer 1+2 tick
    if port == 0x3C5: return vga.seq[vga.seqi]
    if port == 0x3D5: return vga.crtc[vga.crtci]
    if port == 0x3CF: return vga.gc[vga.gci]
    if 0x200 <= port < 0x400: log('in %x' % port)
    return 0xFF
def port_out(uc, port, size, val, ud):
    if size == 2 and port in (0x3C4, 0x3D4, 0x3CE):   # index/data word write
        port_out(uc, port, 1, val & 255, ud); port_out(uc, port + 1, 1, val >> 8, ud); return
    if port == 0x3C4: vga.seqi = val & 7
    elif port == 0x3C5: vga.seq[vga.seqi] = val
    elif port == 0x3D4: vga.crtci = val & 0x1F
    elif port == 0x3D5: vga.crtc[vga.crtci] = val
    elif port == 0x3CE: vga.gci = val & 15
    elif port == 0x3CF:
        if vga.gci < 9: vga.gc[vga.gci] = val
    elif port == 0x3C8: vga.dacw = val; vga.dacc = 0
    elif port == 0x3C9:
        vga.dac[(vga.dacw * 3 + vga.dacc) % 768] = val & 63; vga.dacc += 1
        if vga.dacc == 3: vga.dacc = 0; vga.dacw = (vga.dacw + 1) & 255
    elif port == 0x3C0: pass
    elif port in (0x40, 0x43, 0x20, 0xA0, 0x21, 0xA1): pass
    else: log('out %x=%x' % (port, val))
logd = {}
def log(s):
    if logd.get(s, 0) < 3: print('[io]', s)
    logd[s] = logd.get(s, 0) + 1
mu.hook_add(UC_HOOK_INSN, port_in, None, 1, 0, UC_X86_INS_IN)
mu.hook_add(UC_HOOK_INSN, port_out, None, 1, 0, UC_X86_INS_OUT)

# ---------------- memory / extender state ----------------
mu.mem_write(0, SEG)
def w32(a, v): mu.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))
def r32(a): return struct.unpack('<I', mu.mem_read(a, 4))[0]
def r16(a): return struct.unpack('<H', mu.mem_read(a, 2))[0]
def w16(a, v): mu.mem_write(a, struct.pack('<H', v & 0xFFFF))
STACK_TOP = 0x12B30 + 0x6400
w32(0, 0x12B30 + 0x6400); w32(4, 0x9F000)    # low heap [cur, limit]
w32(8, 0); w32(0xC, 0)                       # extended heap, set by int 31/501
w32(0x18, 0)                                  # ds base
for o in (0x1C, 0x1E, 0x20, 0x22): w16(o, 0x10)
w32(0x28, STUB_GET); w32(0x2C, STUB_SET)
mu.mem_write(STUB_GET, b'\xC3'); mu.mem_write(STUB_SET, b'\xC3')
# GDT: null, code, data, data, data, gs(IVT)
def desc(base, limit, acc, fl): return struct.pack('<HHBBBB', limit & 0xFFFF, base & 0xFFFF, base >> 16 & 255, acc, fl << 4 | limit >> 16 & 15, base >> 24)
GDT = 0xF90000
gdt = b'\0' * 8 + desc(0, 0xFFFFF, 0x9A, 0xC) + desc(0, 0xFFFFF, 0x92, 0xC) * 3 + desc(IVT, 0xFFFF, 0x92, 0xC)
mu.mem_write(GDT, gdt)
mu.reg_write(UC_X86_REG_GDTR, (0, GDT, len(gdt) - 1, 0))
mu.reg_write(UC_X86_REG_CR0, mu.reg_read(UC_X86_REG_CR0) | 1)
for r, sel in ((UC_X86_REG_CS, 8), (UC_X86_REG_DS, 0x10), (UC_X86_REG_ES, 0x10), (UC_X86_REG_SS, 0x10), (UC_X86_REG_FS, 0x10), (UC_X86_REG_GS, 0x28)):
    mu.reg_write(r, sel)
mu.reg_write(UC_X86_REG_ESP, STACK_TOP)
mu.reg_write(UC_X86_REG_EFLAGS, 0x202)

handlers = {}
def getvec(uc, a, s, u):
    uc.reg_write(UC_X86_REG_EDX, handlers.get(uc.reg_read(UC_X86_REG_EBX) & 255, 0))
def setvec(uc, a, s, u):
    handlers[uc.reg_read(UC_X86_REG_EBX) & 255] = uc.reg_read(UC_X86_REG_EDX)
mu.hook_add(UC_HOOK_CODE, getvec, None, STUB_GET, STUB_GET)
mu.hook_add(UC_HOOK_CODE, setvec, None, STUB_SET, STUB_SET)
# program name copy (reads PSP/env in the real extender)
def getname(uc, a, s, u):
    edi = uc.reg_read(UC_X86_REG_EDI); uc.mem_write(edi, b'EPIDEMIC.EXE\0')
    esp = uc.reg_read(UC_X86_REG_ESP); uc.reg_write(UC_X86_REG_EIP, r32(esp)); uc.reg_write(UC_X86_REG_ESP, esp + 4)
mu.hook_add(UC_HOOK_CODE, getname, None, 0x12148, 0x12148)

# ---------------- DOS32 services (int 33) ----------------
files = {}; nexth = [5]; pos = {}
def B(o): return r16(0x400 + o)  # reg block lives at 0x494.. (ax=4a0 bx=494 cx=49c dx=498 ds=4a8)
R = dict(bx=0x494, dx=0x498, cx=0x49C, ax=0x4A0, ds=0x4A8)
def setcf(uc, cf):
    f = uc.reg_read(UC_X86_REG_EFLAGS); uc.reg_write(UC_X86_REG_EFLAGS, (f & ~1) | cf)
mem_alloc = [0x400000]
def intr(uc, no, ud):
    eip = uc.reg_read(UC_X86_REG_EIP)
    if no == 0x31:
        ax = uc.reg_read(UC_X86_REG_EAX) & 0xFFFF
        if ax == 0x501:
            size = (uc.reg_read(UC_X86_REG_EBX) & 0xFFFF) << 16 | (uc.reg_read(UC_X86_REG_ECX) & 0xFFFF)
            a = mem_alloc[0]; mem_alloc[0] += (size + 0xFFF) & ~0xFFF
            uc.reg_write(UC_X86_REG_EBX, a >> 16); uc.reg_write(UC_X86_REG_ECX, a & 0xFFFF)
            uc.reg_write(UC_X86_REG_ESI, 0); uc.reg_write(UC_X86_REG_EDI, 1)
        setcf(uc, 0); return
    if no != 0x33: print('int %x?' % no); return
    al = uc.reg_read(UC_X86_REG_EAX) & 255
    ax = r16(R['ax']); ah = ax >> 8
    if al == 0x21:
        if ah == 0x3D:
            a = r16(R['ds']) * 16 + r16(R['dx']); nm = bytes(uc.mem_read(a, 80)).split(b'\0')[0].decode('latin1')
            base = os.path.basename(nm.replace('\\', '/')).lower(); fp = next((os.path.join(os.path.dirname(exe_path), f) for f in os.listdir(os.path.dirname(exe_path)) if f.lower() == base), None)
            if not fp: print('open fail', nm); w16(R['ax'], 2); setcf(uc, 1); return
            files[nexth[0]] = open(fp, 'rb').read(); pos[nexth[0]] = 0; w16(R['ax'], nexth[0]); nexth[0] += 1; setcf(uc, 0); print('open', base)
        elif ah == 0x3E: setcf(uc, 0)
        elif ah == 0x3F:
            h = r16(R['bx']); n = r16(R['cx']); a = r16(R['ds']) * 16 + r16(R['dx'])
            d = files[h][pos[h]:pos[h] + n]; uc.mem_write(a, d); pos[h] += len(d); w16(R['ax'], len(d)); setcf(uc, 0)
        elif ah == 0x42:
            h = r16(R['bx']); off = struct.unpack('<i', struct.pack('<HH', r16(R['dx']), r16(R['cx'])))[0]; o = ax & 255
            pos[h] = (0 if o == 0 else pos[h] if o == 1 else len(files[h])) + off
            w16(R['ax'], pos[h] & 0xFFFF); w16(R['dx'], pos[h] >> 16); setcf(uc, 0)
        elif ah == 0x4C: print('exit', ax & 255); uc.emu_stop(); finished[0] = True
        else: print('dos %02x?' % ah); setcf(uc, 1)
    elif al == 0x10:
        pass
    elif al == 0x33:
        w16(R['ax'], 0)   # no mouse
    elif al == 0x16:
        w16(R['ax'], 0)
    else: print('int33 al=%x' % al)
finished = [False]
mu.hook_add(UC_HOOK_INTR, intr)
lvlw = []
if os.environ.get('LEVELS'):
    mu.hook_add(UC_HOOK_MEM_WRITE, lambda uc, ac_, a, sz, v, u: lvlw.append((tick[0], a - 0x1615, v & 255, uc.mem_read(a, 1)[0])), None, 0x1615, 0x1634)
snaps = []
tk = []
if os.environ.get('TICKLV'):
    mu.hook_add(UC_HOOK_CODE, lambda uc, a, sz, u: tk.append(bytes(uc.mem_read(0x1615, 16))), None, 0x3A74, 0x3A74)
if os.environ.get('STARS'):
    mu.hook_add(UC_HOOK_CODE, lambda uc, a, sz, u: snaps.append(bytes(uc.mem_read(0xB521, 18 * 64))) if len(snaps) < 400 else None, None, 0x122DA, 0x122DA)
hits = {}
def hitcb(uc, a, sz, u): hits[a] = hits.get(a, 0) + 1
for _a in (0x81ce, 0x825d, 0x839e, 0x893e, 0x3a74, 0x3a8d, 0x89a7, 0x8257):
    mu.hook_add(UC_HOOK_CODE, hitcb, None, _a, _a)

# ---------------- scheduler ----------------
IPT = 150000   # instructions per timer tick
def inject(uc, handler):
    esp = uc.reg_read(UC_X86_REG_ESP) - 12
    fl = uc.reg_read(UC_X86_REG_EFLAGS); eip = uc.reg_read(UC_X86_REG_EIP)
    uc.mem_write(esp, struct.pack('<III', eip, 8, fl))
    uc.reg_write(UC_X86_REG_ESP, esp); uc.reg_write(UC_X86_REG_EIP, handler)
    uc.reg_write(UC_X86_REG_EFLAGS, fl & ~0x200)
SC = {'F1': 0x3B, 'F2': 0x3C, 'F3': 0x3D, 'F4': 0x3E, 'F5': 0x3F, 'F6': 0x40, 'F7': 0x41, 'F8': 0x42, 'F10': 0x44, 'ESC': 1, 'ENTER': 0x1C, 'SPACE': 0x39,
      '+': 0x4E, '-': 0x4A, '1': 2, '2': 3, '3': 4}
events = {}
for it in filter(None, script.split(',')):
    t, a = it.split(':', 1); events.setdefault(int(t), []).append(a)
last = max(events) if events else 100
mu.reg_write(UC_X86_REG_EIP, 0xDA3C)
eip = 0xDA3C
IRQ11 = True   # GUS timer IRQ drives the sound engine tick
nshot = 0
prof = {}
while tick[0] <= last and not finished[0]:
    for a in events.get(tick[0], []):
        if a == 'shot': vga.frame().save(os.path.join(outdir, 'f%05d.png' % tick[0])); print('shot', tick[0], 'eip %x' % mu.reg_read(UC_X86_REG_EIP))
        elif a.startswith('key:'):
            kbd[0] = SC[a[4:]]
            if 1 in handlers: inject(mu, handlers[1])
            events.setdefault(tick[0] + 3, []).append('brk:' + a[4:])
        elif a.startswith('brk:'):
            kbd[0] = SC[a[4:]] | 0x80
            if 1 in handlers: inject(mu, handlers[1])
        elif a == 'dbg': print('dbg', tick[0], 'key', mu.mem_read(0x12928, 3).hex(), 'IF', mu.reg_read(UC_X86_REG_EFLAGS) & 0x200, 'eip %x' % mu.reg_read(UC_X86_REG_EIP))
        elif a == 'glyphs':
            import json
            SENT = 0xF80020; mu.mem_write(SENT, b'\xC3'); done = []
            hk = mu.hook_add(UC_HOOK_CODE, lambda uc, ad, sz, u: uc.emu_stop(), None, SENT, SENT)
            saved = {r: mu.reg_read(r) for r in (UC_X86_REG_EIP, UC_X86_REG_ESP, UC_X86_REG_EFLAGS, UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP)}
            outg = {}
            for name, tbl in (('title', 0xA74F), ('comment', 0xA753)):
                ebp = r32(tbl); res = {}
                for code in range(32, 127):
                    for pl in range(4): vga.planes[pl][:] = bytes(65536)
                    mu.mem_write(0xF70000, bytes([code, 0]))
                    esp = saved[UC_X86_REG_ESP] - 64; mu.mem_write(esp, struct.pack('<I', SENT))
                    mu.reg_write(UC_X86_REG_ESP, esp); mu.reg_write(UC_X86_REG_EDX, 0xF70000); mu.reg_write(UC_X86_REG_EBX, 5); mu.reg_write(UC_X86_REG_EBP, ebp)
                    mu.reg_write(UC_X86_REG_EDI, (40 * 80 + 30) * 4); mu.reg_write(UC_X86_REG_EFLAGS, 0x2)
                    vga.seq[4] = 0x06
                    try: mu.emu_start(0x8F7A, 0xFFFFFFFF, count=200000)
                    except UcError as e: res = res; continue
                    w_ = r32(ebp + code * 8 + 4) & 255 if r32(ebp + code * 8) != 0xFFFFFFFF else 0
                    if r32(ebp + code * 8) == 0xFFFFFFFF: continue
                    pts = []
                    for y in range(30):
                        for x in range(24):
                            v = vga.planes[x & 3][(40 + y - 6) * 80 + 30 + (x >> 2)]
                            if v: pts += [x, y - 6, v]   # y relative to the draw origin row
                    res[code] = [w_, pts]
                outg[name] = res; print(name, len(res), 'glyphs')
            json.dump(outg, open(os.path.join(outdir, 'glyphs.json'), 'w'))
            mu.hook_del(hk)
            for r, v in saved.items(): mu.reg_write(r, v)
        elif a == 'vram':
            from PIL import Image
            st = int(os.environ.get('STRIDE', '80')); rows = 65536 // st; img = bytearray(st * 4 * rows)
            for y in range(rows):
                for x in range(st * 4): img[y * st * 4 + x] = vga.planes[x & 3][y * st + (x >> 2)]
            im = Image.frombytes('P', (st * 4, rows), bytes(img)); im.putpalette([min(255, v * 4 + (v >> 4)) for v in vga.dac]); im.save(os.path.join(outdir, 'vram_%d.png' % tick[0]))
        elif a.startswith('mem:'):
            _, ad, ln = a.split(':'); open(os.path.join(outdir, 'mem_%s_%d.bin' % (ad, tick[0])), 'wb').write(bytes(mu.mem_read(int(ad, 16), int(ln))))
        elif a == 'dac': open(os.path.join(outdir, 'dac_%d.bin' % tick[0]), 'wb').write(bytes(vga.dac))
        elif a == 'sprites':
            import json
            SENT = 0xF80020; mu.mem_write(SENT, b'\xC3')
            hk = mu.hook_add(UC_HOOK_CODE, lambda uc, ad, sz, u: uc.emu_stop(), None, SENT, SENT)
            saved = {r: mu.reg_read(r) for r in (UC_X86_REG_EIP, UC_X86_REG_ESP, UC_X86_REG_EFLAGS, UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP)}
            outs = {}
            for name, cell in [('cur%d' % i, c) for i, c in enumerate((0x94F6, 0x94FA, 0x94FE, 0x9502, 0x9506, 0x950A))] + [('btn%d%s' % (i // 2, 'np'[i % 2]), 0x950E + 4 * i) for i in range(18)]:
                code = r32(cell)
                for pl in range(4): vga.planes[pl][:] = bytes(65536)
                vga.trace = {}; vga.seq[4] = 0x06
                esp = saved[UC_X86_REG_ESP] - 64; mu.mem_write(esp, struct.pack('<I', SENT))
                mu.reg_write(UC_X86_REG_ESP, esp); mu.reg_write(UC_X86_REG_ESI, code); mu.reg_write(UC_X86_REG_EDI, 100 * 320 + 100); mu.reg_write(UC_X86_REG_EFLAGS, 0x2)
                try: mu.emu_start(0x1192C, 0xFFFFFFFF, count=200000)
                except UcError as e: print('sprite fail', name, e); vga.trace = None; continue
                tr = vga.trace; vga.trace = None
                xs = [k[0] for k in tr]; ys = [k[1] for k in tr]
                x0, y0 = min(xs), min(ys); w, h = max(xs) - x0 + 1, max(ys) - y0 + 1
                outs[name] = dict(x=x0 - 100, y=y0 - 100, w=w, h=h, p=[tr.get((x0 + i % w, y0 + i // w), -1) for i in range(w * h)])
                print(name, outs[name]['x'], outs[name]['y'], w, h)
            json.dump(outs, open(os.path.join(outdir, 'sprites.json'), 'w'))
            mu.hook_del(hk)
            for r, v in saved.items(): mu.reg_write(r, v)
        elif a == 'stop': finished[0] = True
    try:
        mu.emu_start(mu.reg_read(UC_X86_REG_EIP), 0xFFFFFFFF, count=IPT)
    except UcError as e:
        print('ERR', e, 'eip %x' % mu.reg_read(UC_X86_REG_EIP)); vga.frame().save(os.path.join(outdir, 'err.png')); break
    eip = mu.reg_read(UC_X86_REG_EIP)
    if os.environ.get('PROF'):
        a0, a1 = map(int, os.environ['PROF'].split('-'))
        if a0 <= tick[0] <= a1: prof[eip] = prof.get(eip, 0) + 1
        if a0 <= tick[0] <= a1 and tick[0] % 10 == 0: print('T', tick[0], 'crtc start %02x%02x' % (vga.crtc[0xC], vga.crtc[0xD]), 'off', vga.crtc[0x13], 'seq2', vga.seq[2], 'eip %x' % eip)
    tick[0] += 1
    if 0 in handlers and (not IRQ11 or tick[0] % 2) and mu.reg_read(UC_X86_REG_EFLAGS) & 0x200:
        inject(mu, handlers[0]); eip = mu.reg_read(UC_X86_REG_EIP)
    elif IRQ11 and 11 in handlers and mu.reg_read(UC_X86_REG_EFLAGS) & 0x200:
        inject(mu, handlers[11])
print('hits', {hex(k): v for k, v in hits.items()}, 'c10/c11', mu.mem_read(0xc10, 2).hex(), 'IF', mu.reg_read(UC_X86_REG_EFLAGS) >> 9 & 1)
if lvlw: open(os.path.join(outdir, 'levels.txt'), 'w').write('\n'.join('%d %d %d %d' % w for w in lvlw))
if tk: open(os.path.join(outdir, 'ticklv.bin'), 'wb').write(b''.join(tk))
if snaps: open(os.path.join(outdir, 'stars.bin'), 'wb').write(b''.join(snaps))
print('done tick', tick[0], 'eip %x' % eip, 'handlers', {k: hex(v) for k, v in handlers.items()})

if prof: print(sorted(((hex(k), v) for k, v in prof.items()), key=lambda x: -x[1])[:15])
