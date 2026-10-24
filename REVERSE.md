# Reversing EPIDEMIC.EXE

Notes on how the original was taken apart. Addresses are offsets in the unpacked 32-bit segment ("seg32", linear
base 0 in the emulator) unless stated otherwise.

## 1. Container

```
EPIDEMIC.EXE  242,329 bytes
  MZ image, 35,659 bytes  LZEXE-style stub (header "MZK", no LZ91 tag), compresses the rest
  overlay, 206,670 bytes  resources, addressed from the END of the file
```

* **Stub.** Copies itself up, runs a bit-reader decompressor, then jumps to the real entry. `tools/unpack.py` loads
  the image at a fake segment in unicorn (16-bit), applies the relocation, and stops at the first instruction that
  executes below the stub's high segment (`cs:ip = 01de:02ee` after decompression).
* **Extender.** The unpacked 16-bit code is a DOS32-style extender (messages: "DPMI host is not 32bit!!!", "Couldn't
  enable A20 gate!!!"...). It sets up a GDT, enters protected mode and jumps to the flat 32-bit program, whose segment
  starts at real-mode segment `0x1263` (file-image offset `0x2630`). seg32 begins with extender data/GDT; code lives in
  `0x3200-0x9800` (sound engine) and `0xa200-0x12b08` (UI), data is interleaved.
* **Entry.** `0x614` hooks two real-mode vectors, then `jmp 0xda3c`, the program's `main`.
* **DOS32 services.** The program talks to the extender with `int 33h`: `al` = real-mode interrupt number, registers in a
  block at `0x494` (`bx 494, dx 498, cx 49c, ax 4a0, ds 4a8`); results come back in the same block plus CF. It uses
  `21h` (open/read/seek/close), `10h` (video mode), `16h`, and `33h` (mouse). `int 31h ax=501h` allocates the 768 KB
  extended block. `[0x28]/[0x2c]` are get/set interrupt vector entry points; the program installs IRQ0 (`0x9200`,
  70 Hz vsync/timing), IRQ1 (`0x12a40`, keyboard) and the sound card IRQ (`0x81ce`).

## 2. Overlay resources and the LZ format

All resources are located by "N bytes before end of file". Compressed ones are an LZ stream:

* 16 flag bits per 16-bit word, LSB first; `1` = match, `0` = literal byte (sentinel-bit trick: `stc; rcr`).
* Match word: `distance = w >> 4` (12 bit), `length = (w & 15) + 3`; length 18 reads one more byte `b`
  (`length = 18 + b`); `b == 0xFF` ends the stream.
* Streams are back to back, so each stream's end is the next stream's start.

| from end | size | content |
|---|---|---|
| `0x3274e 0x2ef23 0x2b6f6 0x27f7c` | 4 x 32000 | player screen, four mode-X planes (320x400) |
| `0xb618` | 128000 | "ICE & REN PRESENT" (320x400 chunky) |
| `0x4996` | 128000 | help screen |
| `0x77ac` | 64000 | logo band with knots (320x200) |
| `0x68d2` | 64000 | graffiti head (320x200) |
| `0x1a8a9`, `0x139fe`, `0x145eb` | 64000 / 64000 / 28209 | landscape, chrome scroller font, scroller code: the `HIYA! SYNTAX...` scroller; not reached in the normal flow |
| `0x1f489`, `0x1ccf5` | 0x23a0, 0x244c (raw) | the two small text fonts, as compiled blitters (see 4) |
| 24 raw chunks, 0x1d0e9... | ~1 KB each | compiled sprite blitters: 6 cursor frames + 18 button sprites |
| last 10 bytes | | sound-card configuration written by `SETUP.EXE` |

The three `EPIDEMIC.00x` files are not packed: plain concatenations of S3M modules (`SCRM` at +0x2c, title at +0).
Song table: 18 records of 16 bytes at `0xa75f` = `{author*, file descriptor*, comment*, title*}`.

## 3. Palettes

DAC data (6-bit) sits in seg32 at `0xb9b5` (main, the red/blue player), `0xbcb5` (ICE & REN), `0xbfb5`, `0xc2b5`
(gold logo), `0xc5b5` (head), `0xc8b5` (help), each 768 bytes, 0x300 apart. The text colours are not in the palette:
the title gradient (entries `0xa0-0xaf`) and the comment colours (`0xca-0xcd`) are fade targets at `0xa3a7` / `0xa40b`.
The unpacker/emulator proves the assignments: the DAC was dumped at several ticks and matched byte-for-byte.
Fades step the DAC by 1 per vsync for 64 frames (`0x10c26` out, `0x10c8d` in, subtractive/clamped).

## 4. Text: compiled blitters

The two text fonts are not bitmaps. Each glyph is a 8-byte header (`len, width, height, ascii`) followed by x86 that
writes pixels (`mov [edi],imm8`, `add edi,0xa0`, `rol al,1 / out dx,al / adc edi,0` to step planes). The draw routine
`0x8f7a` (via `0x8fbc`/`0x1192c`) calls the blitter with `edi` = pixel address and `al` = map mask `1 << (x & 3)`.
Rather than decode the code, `emu.py` runs the real routine per ASCII code and reads the planes back
(`tools/emu.py ... glyphs`). Result: 5-pixel advance, 12 rows, title font colours `0xa0+row`, comment font `0xcb-0xcd`.
Missing glyphs (e.g. the box-drawing bars in some comments) advance by 5 and draw nothing; `\n` is +16 rows.
The same trick extracts the cursor and button sprites (`... sprites`).

Placement (pixel = `y*320+x`): title `(94,236)`, author `(94,263)`, comment `(80,37)`, 5 lines max.

## 5. Display model

Screen is the classic tweaked 320x400 mode X (unchained, byte mode, `max scan line = 0`). Double buffering with CRTC
start address (`0x96ea/0x96ec`), vsync flag `[0x96d2]` set by the IRQ0 handler, which also calls the per-frame draw
routine at `0x106c5`.

## 6. Main screen, per frame (`0x106c5`)

* **Position strip** (`0x10b71`): 32 cells of 8x4 px at `(32,24)`, cell `song*31/17` highlighted (`0x63`, others `0x6e`).
* **VU bars** (`0x10969`): up to 16 channels, 8 px wide, 12 px pitch, bar `k` at `x = 76 + 4*(16-n) + 12*k`, drawn upward
  from y=122 every other row, `height = (level * 0xb4) >> 8` steps, colour `0xd3 + step`, remaining of 45 steps cleared.
* **Particle field** (`0x122da`/`0x123ab`): 64 records of 9 words at `0xb521`:
  `colour, x, y, prevx, prevy, ax, ay, vx, vy` (16-bit unsigned positions, signed velocities).
  Per frame: `y += vy` with bounce (`vy = -vy; y = 0xffff` on wrap), `vy += ay`; `x += vx` bouncing at 0 / 0xffff,
  `vx += ax`. Pixel = `(92 + (x*136 >> 16), 282 + (y*73 >> 16))`. The JS reproduces 371 consecutive recorded updates
  with zero differences.
* **Text fades:** song change (`0x10530`) = title fade out (64 frames), comment fade out (64), load + redraw, title fade in
  (64), comment fade in (64); input is blocked meanwhile (one key stays buffered).

### VU level rules (engine `0x3f09-0x4118` and tick code)

Per channel there are two bytes: running volume (`[1024+ch]`) and displayed level (`[1615+ch]`).

1. Row names an instrument: both = instrument default volume (table `0x2a6c`, equal to the S3M sample volume).
2. Volume column present: both = that volume.
3. Fine slide (`DxF`, `DFx`): applied once at row start, level = new volume.
4. Other `D/K/L` slides: every tick after the first, `volume += step`, level = volume.
5. Every engine tick: `level = max(level-1, 0)` (before the above).

Ticks run at `tempo * 0.4` Hz. Validated by logging every write to `[0x1615..0x1634]` in the emulator and replaying the
S3M pattern data in a lock-step model: exact for the first 756 ticks of the intro tune, then one tick off (an emulator
timing artefact in how I inject the sound IRQ, not a rule difference).

## 7. Intro (`0xef83-0xf27d`, `0xff00`, `0xff8b`)

1. **ICE & REN PRESENT.** Image copied planar into VRAM page 1; CRTC start += `0xa0` (2 rows) per frame for 200 frames
   while the palette fades in; hold 100 frames; fade out 64.
2. **Gold "EPIDEMIC".** `CRTC[0x13] = 0xa0` (virtual width 1280 px), `CRTC[9] |= 1` (scan doubling), map mask `0xf`.
   Each frame one source column x of the logo (rows 111-160) is written as one byte per pixel at plane-byte column
   `x + 0x50`, repeated over 4 rows; every byte lands in all four planes, i.e. 4x horizontal zoom, and scan doubling
   plus the 4-row repeat gives the tall letters. The CRTC start then advances by 1 per frame (400 frames), so the
   zoomed band scrolls by. After column 319 zeros are written.
3. **Head.** Chunky image copied into VRAM rows 200-399; CRTC start row starts at 370 and bounces on row 170:
   16.16 fixed point, `vel -= 0x2000` per frame, `pos += vel`, on `pos <= 170` set `pos = 170`,
   `vel = ((-vel) >> 3) * 5`. 300 frames, then fade out.

## 8. Mouse (`0x10e44`, `int 33h` handler)

Driver callback (real-mode stub at ext16 `0x7a0` -> `int 32h` -> `0x10e44`), event mask `ax`:

* bit 0 move: store `cx,dy` and advance the cursor frame (0-7 -> 4 distinct rat frames, two moves each).
* bit 1 press: hit-test 9 boxes at `0x96f5` (`y0,y1,x0,x1`, tested against `x+5, y`), set flag `[0x9785+i]` and swap in
  the pressed sprite (`0x9792` pairs normal/pressed).
* bit 2 release: restore the normal sprite.

The main loop (`0x10081`) consumes the flags: 0 play, 1 stop, 2 pause, 3 prev track, 4 next track, 5 prev song, 6 next song,
7 DOS shell, 8 exit. While loading (`[0xa3a1]`) the cursor is a two-frame gem instead of the rat.

## 9. Keyboard

IRQ1 handler maps scancodes through a table at `0x12940` (F1-F10 -> 1-10, Esc -> 0x0e). Main loop `0x102c0`:
1 help, 2 play, 3 stop, 4 pause, 5/6 prev/next track (also arrows `0x15-0x18`), 7/8 prev/next song, 9 shell, 10 exit,
`+ = -` songs, digits 1-9 and the letters in `"qwertyuio"` (index + 9) pick songs 1-18.

## 10. The emulator (`tools/emu.py`)

unicorn, 32-bit protected mode, flat GDT (code/data selectors `8/0x10/0x18/0x20`, `gs` = virtual IVT).
Models only what the program touches:

* DOS32 `int 33h` (file I/O against the real files, CF handling), `int 31h/501h`, vector get/set stubs, program-name copy
  (hook on `0x12148`).
* VGA: 4 planes, map mask, unchained/chain-4 writes, write mode 0/1, DAC, CRTC start/offset, `3da` retrace toggle,
  frame dump honouring start address and stride.
* Time: one slice of 150k instructions per tick; alternate ticks inject IRQ0 (vsync/draw) and the GUS IRQ.
  The config in the EXE tail selects a Gravis Ultrasound; instead of emulating it, port `0x246` returns "timer 1+2 fired"
  and the card IRQ handler is called every other tick, which is enough to run the real sound engine and its visuals.
* Keyboard: inject IRQ1 with make/break scancodes.

Analysis actions: `shot`, `vram` (full virtual screen), `dac`, `mem:addr:len`, `glyphs`, `sprites`, and env-driven traces
(`LEVELS`, `TICKLV`, `STARS`, `PROF=a-b` for hot-spot profiling).

Disassembly was done with capstone recursive descent from `main` (`rd.py`, not kept: ~4,000 instructions reached);
indirect calls and data-embedded code were found by profiling the emulator and following `ds` accesses.

## 11. Dead ends worth knowing

* My first S3M mixer was written from the spec; the original's engine is an S3M player of its own (converted pattern
  format with up to 4 effect words per cell, `0x358b` jump table), so exact audio would mean porting it plus a GUS model.
  libopenmpt replaced it.
* The first palette guess (`0xc2b5` for ICE & REN) was wrong; dumping the live DAC settled it.
* Early gold-phase frames looked broken because the VGA model ignored the CRTC virtual width and scan doubling; fixing
  the model, then reading `f27e`, explained the effect.
