# EPIDEMIC (Renaissance / iCE, 1994) - asm snippets worth reading

All addresses are offsets in the unpacked flat 32-bit segment ("seg32") of `EPIDEMIC.EXE` (a LZEXE-style stub, then a DOS32-style extender, then the 32-bit program, dumped
by `tools/unpack.py` at file-image offset `0x2630`). Listings were produced with capstone (`CS_MODE_32`) on that dump. The one listing taken from the overlay, the
glyph, gives a file offset instead. Code is 386-era hand-written assembly (no compiler idioms: `jecxz`, `loop`, `stosw`, `rep stosw`, `mul ah`), with the VGA
registers poked directly. Nothing here is MMX or x87: the whole program is integer.

The material is small (the program is a music disk, about 4,000 reachable instructions in the UI half, per `REVERSE.md`), so this is a short list of the tricks
that are actually verifiable in the code.

---

## 1. A PLL for the vertical retrace, built from the PIT reload value (0x9200)

The program does not poll vsync. It reprograms timer 0 and uses IRQ0 itself as the frame interrupt, then keeps it locked to the retrace with a few instructions:

```
920a: mov dx, 0x3da
920e: in  al, dx            ; input status 1
920f: and eax, 8            ; bit 3 = vertical retrace
9212: dec eax
9213: neg eax               ; retrace on: -7     retrace off: +1
9215: add word [0x9251], ax ; PIT reload value (the divisor), adjusted
921c: in  al, dx / test al, 8 / je 0x921c   ; wait for the retrace to start
9221: mov ax, [0x9251]
9227: out 0x40, al / mov al, ah / out 0x40, al  ; re-arm the PIT with the new divisor
922d: call 0x106c5          ; draw the frame
```

`and 8 / dec / neg` turns a flag bit into +1 or -7 without a branch. If the interrupt arrives while the retrace is already on (late), the divisor drops by 7; if it arrives before (early) it
grows by 1. The handler then spins until the retrace starts and reloads the PIT right there, so the next IRQ0 is one divisor after the start of the retrace. It is a
first-order tracking loop with asymmetric gain, and the frame routine always starts in the retrace. The divisor lives at `0x9251`, which is the 16-bit immediate of an
`mov ax, 0x4100` in the 16-bit stub that follows (decoded as 16-bit code at 0x9250: `b8 00 41 e6 40 ...`); the stub looks like an older real-mode copy of the same handler (it
adds to `cs:[0]`, a patched address). I only verified the 32-bit handler; the initial divisor in the image is `0x4100` (a 71.7 Hz timer rate if the PIT is at 1.193182 MHz,
my arithmetic, not read from the code).

## 2. Using the carry flag as the bounce test (0x12302..0x12366, particle field)

Positions are 16-bit unsigned, velocities are 16-bit signed, and a particle must reflect at 0 and at 65535. There is no compare: the add that moves it sets the flag.

```
1230a: mov cx, [esi+0x10]          ; vy
1230e: or  cx, cx
12311: jns 0x1231b
12313: add [esi+4], cx             ; vy < 0: y += vy ... (unsigned add of a negative = subtract)
12317: jb  0x1232b                 ; carry out: no underflow happened, keep going
12319: jmp 0x12321
1231b: add [esi+4], cx             ; vy >= 0
1231f: jae 0x1232b                 ; no carry: still inside
12321: neg word [esi+0x10]         ; wrapped: reverse velocity
12325: mov word [esi+4], 0xffff    ; and clamp
1232b: mov cx,[esi+0xc] / add [esi+0x10],cx    ; vy += ay
```

For a negative velocity the sixteen-bit add *produces* a carry when the position stays >= 0 (a - |v| in two's complement), and no carry exactly when it would have gone below 0.
So the `jb` / `jae` pair, picked by the sign of the velocity, replaces both range checks. The code writes `0xffff` for a wrap on y in both directions and `0` / `0xffff` on x
(0x1234e / 0x1235c). The JS port transcribes this with explicit `cy` variables, and with the table taken from the EXE it matched 371 recorded frames with zero differences
(`REVERSE.md`).

## 3. Particle pixel address with no multiplication table (0x1236e..0x1239d)

Plotting a pixel in mode X needs the plane (`x & 3`), the byte offset (`(y*320 + x) >> 2`) and a map-mask write. Scaling the 16-bit state to the 136x73 pane and addressing happen together:

```
1236e: movzx eax, word [esi+2]     ; x (16 bit)
12372: movzx ebx, word [esi+4]     ; y
12379: imul ebx, ebx, 0x49 / shr ebx, 16     ; y * 73 >> 16
1237f: imul ebx, ebx, 0x140                   ; * 320 (the row in a linear 320 pitch)
12385: imul eax, eax, 0x88 / shr eax, 16      ; x * 136 >> 16
1238e: add ebx, eax / shr ebx, 2              ; byte offset in the plane
12393: and eax, 3                             ; plane
12396: mov al, [eax+0xb9a1]                   ; map mask table 01 02 04 08
1239c: out dx, al                             ; select the plane
1239d: mov [edi+ebx], cl                      ; pixel
```

The scale `x*136>>16` is a multiply-high instead of a division, and because 320 is a multiple of 4 the plane number is just the low two bits of x after scaling, so `& 3` on the
*scaled* x selects the plane for the whole linear address. Erasing (0x123ab) is cheaper still: it sets the map mask once to `0xf` (`mov ax,0xf02 / out dx,ax`) and clears each old pixel with a
single byte write, which hits all four planes. It relies on the background being colour 0 in that pane.

## 4. Saturating decrement without a branch: `sub 1 / adc 0` (0x3ab2, 0x1093b, 0x10a12)

The VU levels decay by 1 per engine tick, stopping at 0:

```
3aad: mov ebp, 0x1f
3ab2: sub byte [ebp+0x1615], 1     ; borrow if it was 0
3aba: adc byte [ebp+0x1615], 0     ; give it back
3ac2: dec ebp / jns 0x3ab2         ; 32 channels
```

`sub x,1` sets the carry only when x was 0 (result 0xff); `adc x,0` then adds that borrow back and yields 0 again. In two instructions, with no compare or jump, the byte saturates at 0. The same
pair appears in the display routine for the first 16 levels (0x1093b..0x1094a) and for a one-shot counter at `[0xa39d]` (0x10a12). The sequence relies on both operations being in
memory (a read-modify-write on a byte table), which on a 386 is cheaper than loading, testing and branching.

## 5. One `stosw` paints 8 pixels: VU bars in unchained mode (0x109a0..0x109d5)

```
109a0: movzx eax, byte [esi+0x1615]        ; channel level
109a7: mov ah, 0xb4 / mul ah / shr eax, 8  ; level * 180 >> 8 = number of steps
109b2: mov al, 0xd3 / mov ah, al           ; colour, in both bytes
109b9: stosw                               ; two bytes = 8 pixels (map mask 0xf)
109bb: sub edi, ebx                        ; ebx = 0xa2: net -0xa0 = two rows up
109bd: inc al / mov ah, al / loop 0x109b9  ; next colour of the gradient
109c3: mov ecx,0x2d / sub cl, dl / jecxz / xor ax,ax ... stosw ...  ; clear the rest of the 45 steps
```

With the sequencer map mask left at `0xf` (set once at 0x1096d), every VRAM byte is four identical pixels, so a 2-byte `stosw` is an 8-pixel segment and the `-0xa2` after the 2-byte
advance steps the pointer up by exactly one *every other* row (pitch `0xa0` bytes, two scan lines of 80). The scale `*0xb4 >> 8` is `mul ah` with the constant loaded into `ah`
(an 8x8 -> 16 multiply in one instruction), and the clear loop uses `jecxz` to skip when a bar is full. The bar's colour ramp (`0xd3 + step`) is a palette gradient, so the loop
is also the shading.

## 6. Text as code, and the plane carry trick (0x8f7a..0x8ffa and the glyphs)

The small fonts are not bitmaps. In the overlay each glyph (file offset `0x1be90` = `len(EXE) - 0x1f489`, the start of the first of the two font blobs; I did not check which font it is) is a header plus a straight-line
routine:

```
 out dx, al                       ; select the plane (al = map mask)
 mov byte [edi], 0xa0             ; row 0, pixel in this plane
 mov byte [edi+0x50], 0xa1        ; next row (pitch is 0x50 bytes)
 add edi, 0xa0                    ; down 2 rows
 mov byte [edi], 0xa2 ...
 ...
 rol al, 1                        ; next plane
 out dx, al
 adc edi, 0xfffffce0              ; rewind to the top row, +1 byte if the plane wrapped
 mov byte [edi], 0xaa ...
 ...
 ret
```

(Taken from the overlay: the header bytes are `c2 00 05 0c 61 ...`, a 194-byte routine, width 5, height 12, ASCII 0x61; the colour constants `0xa0 + row` are the
title gradient. The `0xfffffce0` is `-0x320` = `-5 * 0xa0`.) The caller prepares the mask:

```
8fe8: and ebx, 3                  ; x & 3 = starting plane
8feb: mov al, [ebx+0x8fb8]        ; 01 02 04 08
8ff1: shl al, 4
8ff4: or  al, [ebx+0x8fb8]        ; the nibble is duplicated: 0x11, 0x22, 0x44, 0x88
8ffa: call esi                    ; call the glyph
```

Why the mask is duplicated: `rol al,1` moves the plane bit up one place, and after plane 3 the *high* nibble copy `0x88` rotates into `0x11` with the carry set. So the `adc edi, imm` that
follows each `rol/out` adds 1 to the destination only when the glyph crossed a 4-pixel byte boundary, with no test at all. The glyph code is then position independent in x (any `x & 3` start works with
the same code), and no pixel is ever masked or shifted at run time: every pixel is a `mov byte [edi+k], imm8` with a constant colour. The cost is memory (about 0x23a0 bytes of code for one font),
which a music disk with 3 MB of modules does not care about. The port could not reuse this directly (it cannot run x86), so `tools/emu.py` calls each glyph in the emulator and the port keeps
the pixel lists.

## 7. The unrolled column copy for the gold zoom (0xf2b0..0xf954)

```
f2b0: mov al, [esi]                   ; one source pixel (a column of the logo)
f2b2: mov [edi], al                   ; written to 4 rows: this is the 4x vertical repeat
f2b4: mov [edi+0x140], al
f2ba: mov [edi+0x280], al
f2c0: mov [edi+0x3c0], al
f2c6: add esi, 0x140                  ; next logo row (pitch 320)
f2cc: add edi, 0x500                  ; next group of 4 VRAM rows
 ... (50 copies: rows 111-160)
```

The loop over the 50 rows is unrolled completely (counted: 50 loads between 0xf28f and 0xf954), with constant displacements for the 4 rows, so each source pixel costs one load, four stores and two
adds, no loop counter. Entry is guarded by `cmp [0xef73],0x13f / ja 0xf959`, which switches to the same structure with `mov byte [edi+k], 0` once the column index exceeds 319 so the trail clears.
Because the sequencer map mask is `0xf` and CRTC register 9 doubles scan lines (set at 0xf141), those stores produce a 4-pixel-wide, 8-line-tall block per source pixel: the zoom is
the VGA doing the work.

## 8. Branch-free conditional mask (0x404e..0x405d)

```
404e: test byte [ebp+0x1224], 4
4056: setne al
4059: xor ah, ah
405b: sub ah, al                   ; ah = 0 or 0xff
405d: and [ebp+0x1204], ah         ; clear the field if the flag was clear
```

`setne` then `0 - al` builds a byte mask (0x00 / 0xff) from a flag, and the `and` conditionally keeps or clears a channel byte with no jump. The same shape repeats for the next
channel field (0x4064..). It appears in the part of the engine that clears per-channel state for a new row.

## 9. Fades by the byte, with saturation in the loop (0x1088f..0x108c6)

```
1088f: mov ecx, 0x300              ; 768 DAC bytes
10894: mov bl, [0x9d85]            ; step (1 in the image)
108a3: sub [esi], bl               ; fade out: component -= step
108a5: jae 0x108aa
108a7: mov byte [esi], 0           ;   clamp to 0
...
108b5: add [esi], bl               ; fade in: component += step
108b7: mov al, [ebp]               ;   target palette byte
108bb: cmp [esi], al
108bd: jbe 0x108c1
108bf: mov [esi], al               ;   clamp to the target
```

Nothing exotic: the point is the design. The target palette is kept as-is, a working copy is stepped, and the upload goes out in two halves of `0x180` bytes (`out 0x3c8, 0` then `out 0x3c8, 0x80`, 128
entries per frame) so that it fits in the retrace. The fade-in clamp against the target means the same routine fades any of the five palettes. I did not re-derive from this code that a fade lasts 64
frames; that number comes from the emulator measurement in `REVERSE.md`.

## 10. Bounce with fixed-point restitution (0xffb4..0xfff8, the head)

```
ffb4: sub dword [0xef77], 0x2000      ; vel -= 0.125 (16.16)
ffc3: add [0xef73], eax               ; pos += vel
ffc9: cmp dword [0xef73], 0xaa0000    ; floor at row 170
ffdf: neg dword [0xef77]
ffe5: sar dword [0xef77], 3           ; /8
fff6: imul ebx  (ebx = 5)             ; *5  => -vel*5/8
```

Gravity and restitution in four integer instructions; `sar 3` then `imul 5` is `*5/8` with truncation toward minus infinity, which the JS port keeps (`((-hVel)>>3)*5`). The result
`(pos >> 16) * 0x50` is written to the CRTC start-address registers (0xff8c..0xffb3), so the whole animation is a register write per frame.
