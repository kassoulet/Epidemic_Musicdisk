// Minimal ScreamTracker 3 reader: only what the UI needs (orders, channel enable map, instrument volumes, pattern events, speed/tempo).
// Audio is rendered by libopenmpt (vendor/chiptune3.js).
export function parseS3M(buf) {
  const d = new DataView(buf), u8 = new Uint8Array(buf);
  if (String.fromCharCode(...u8.subarray(0x2c, 0x30)) !== 'SCRM') throw new Error('not an S3M');
  const ordNum = d.getUint16(0x20, true), insNum = d.getUint16(0x22, true), patNum = d.getUint16(0x24, true);
  let p = 0x60;
  const orders = [...u8.subarray(p, p + ordNum)]; p += ordNum;
  const insPtr = [...Array(insNum)].map((_, i) => d.getUint16(p + 2 * i, true) * 16); p += 2 * insNum;
  const patPtr = [...Array(patNum)].map((_, i) => d.getUint16(p + 2 * i, true) * 16);
  const enabled = [...Array(32)].map((_, c) => u8[0x40 + c] < 128);          // channel settings: >=128 = unused
  const insVol = insPtr.map(o => (o && u8[o] === 1 ? u8[o + 28] : 0));
  const pats = patPtr.map(o => {
    const rows = [...Array(64)].map(() => []);
    if (!o) return rows;
    let q = o + 2, r = 0;
    while (r < 64) {
      const b = u8[q++];
      if (!b) { r++; continue; }
      const e = { note: 255, ins: 0, vol: -1, cmd: 0, par: 0 };
      if (b & 32) { e.note = u8[q++]; e.ins = u8[q++]; }
      if (b & 64) e.vol = u8[q++];
      if (b & 128) { e.cmd = u8[q++]; e.par = u8[q++]; }
      rows[r][b & 31] = e;
    }
    return rows;
  });
  return { orders, enabled, insVol, pats, speed: u8[0x31] || 6, tempo: u8[0x32] || 125 };
}
