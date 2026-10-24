"""Extract EPIDEMIC.EXE assets -> web/assets. Needs `pip install unicorn` (+ pillow only for emu.py screenshots).
usage: python3 tools/extract.py epidemic/EPIDEMIC.EXE web/assets
EXE = LZEXE-style stub -> DOS32 extender + flat 32-bit program (seg32[0] = unpacked image 0x2630), then an overlay of
resources addressed from the END of the file (LZ stream: 16 flag bits/word, 1=match, match word = dist<<4 | len-3,
len 18 = +1 byte, byte 0xFF ends). Text glyphs are compiled x86 blitters, so they are rendered by running the real
draw routine (0x8f7a) in tools/emu.py."""
import json, os, struct, subprocess, sys, tempfile
exe, out = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
here = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
subprocess.run([sys.executable, os.path.join(here, 'unpack.py'), exe, '8bb'], check=True, cwd=tmp)  # -> tmp/mem.bin
seg = open(os.path.join(tmp, 'mem.bin'), 'rb').read()[0x2630:]
open(os.path.join(tmp, 'seg32.bin'), 'wb').write(seg)
EXE = open(exe, 'rb').read()

def unlz(p):
    o = bytearray(); bp = 0
    def word():
        nonlocal p; v = EXE[p] | EXE[p + 1] << 8; p += 2; return v
    def bit():
        nonlocal bp
        cf = bp & 1; bp >>= 1
        if bp == 0: w = word(); cf = w & 1; bp = 0x8000 | (w >> 1)
        return cf
    while True:
        if not bit(): o.append(EXE[p]); p += 1; continue
        ax = word(); n = (ax & 15) + 3; d = ax >> 4
        if n == 18:
            b = EXE[p]; p += 1
            if b == 255: return bytes(o)
            n = 18 + b
        for _ in range(n): o.append(o[-d])
def res(endoff): return unlz(len(EXE) - endoff)
def planar(d, w, h):  # mode-X planes -> chunky
    pw = w // 4; img = bytearray(w * h)
    for pl in range(4):
        for i in range(pw * h):
            y, x = divmod(i, pw); img[y * w + x * 4 + pl] = d[pl * pw * h + i]
    return bytes(img)
wr = lambda n, d: open(os.path.join(out, n), 'wb').write(d)
os.makedirs(out, exist_ok=True)
wr('main.bin', planar(b''.join(res(o) for o in (0x3274e, 0x2ef23, 0x2b6f6, 0x27f7c)), 320, 400))  # player screen
wr('ice.bin', res(0xb618)); wr('help.bin', res(0x4996)); wr('logo.bin', res(0x77ac)); wr('head.bin', res(0x68d2))

def cstr(p): return seg[p:seg.index(b'\0', p)].decode('cp437')
songs = []
for i in range(18):   # 16-byte records at 0xa75f: author, file desc, comment, title
    a, f, c, t = struct.unpack('<4I', seg[0xa75f + 16 * i:0xa75f + 16 * i + 16])
    songs.append(dict(title=cstr(t), by=cstr(a), note=cstr(c), file=cstr(f + 4)))
pal = lambda o: list(seg[o:o + 768])   # 6-bit VGA DAC
n = struct.unpack('<H', seg[0xb51f:0xb521])[0]   # particle field of the lower pane: 64 records of 9 words at 0xb521
stars = [[w[0], w[1], w[2], w[5], w[6], w[7], w[8]] for w in (struct.unpack('<9H', seg[0xb521 + 18 * i:0xb521 + 18 * i + 18]) for i in range(n))]   # colour, x, y, ax, ay, vx, vy
meta = dict(stars=stars, songs=songs, text=dict(title=list(seg[0xa3a7:0xa3a7 + 48]), comment=list(seg[0xa40b:0xa40b + 12])),   # fade targets for DAC 0xa0..0xaf / 0xca..0xcd
            pal=dict(main=pal(0xb9b5), help=pal(0xc8b5), ice=pal(0xbcb5), logo=pal(0xc2b5), head=pal(0xc5b5)))
subprocess.run([sys.executable, os.path.join(here, 'emu.py'), exe, os.path.join(tmp, 'seg32.bin'), tmp, '3100:key:+,4500:glyphs,4501:sprites,4502:stop'],
               check=True, stdout=subprocess.DEVNULL)
meta['glyphs'] = json.load(open(os.path.join(tmp, 'glyphs.json')))   # {font: {ascii: [advance, [x,y,color,...]]}}
meta['sprites'] = json.load(open(os.path.join(tmp, 'sprites.json')))  # mouse cursor frames (cur0-3 rat, cur4-5 busy) + button n/p sprites, -1 = untouched
meta['buttons'] = [list(struct.unpack('<4I', seg[0x96f5 + 16 * i:0x96f5 + 16 * i + 16])) for i in range(9)]   # y0, y1, x0, x1 (hit box = sprite box; click point = cursor x+5, y)
json.dump(meta, open(os.path.join(out, 'meta.json'), 'w'))
