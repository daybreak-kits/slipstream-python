
import { readFileSync } from "node:fs";
import { inflateSync } from "node:zlib";
const buf = readFileSync(process.argv[2]);
let pos = 8; const chunks = [];
while (pos < buf.length) {
  const len = buf.readUInt32BE(pos); const type = buf.toString("ascii", pos + 4, pos + 8);
  const data = buf.subarray(pos + 8, pos + 8 + len);
  chunks.push({ type, data }); pos += 12 + len;
}
const ihdr = chunks.find((c) => c.type === "IHDR").data;
const width = ihdr.readUInt32BE(0), height = ihdr.readUInt32BE(4);
const bitDepth = ihdr[8], colorType = ihdr[9];
const idat = Buffer.concat(chunks.filter((c) => c.type === "IDAT").map((c) => c.data));
const raw = inflateSync(idat);
const channels = colorType === 6 ? 4 : 3;
const stride = width * channels;
const out = Buffer.alloc(height * stride);
let prev = Buffer.alloc(stride);
for (let y = 0; y < height; y++) {
  const filter = raw[y * (stride + 1)];
  const line = raw.subarray(y * (stride + 1) + 1, y * (stride + 1) + 1 + stride);
  const cur = Buffer.alloc(stride);
  for (let i = 0; i < stride; i++) {
    const a = i >= channels ? cur[i - channels] : 0;
    const b = prev[i];
    const c = i >= channels ? prev[i - channels] : 0;
    let v = line[i];
    if (filter === 1) v = (v + a) & 255;
    else if (filter === 2) v = (v + b) & 255;
    else if (filter === 3) v = (v + ((a + b) >> 1)) & 255;
    else if (filter === 4) { const p = a + b - c; const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); v = (v + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c)) & 255; }
    cur[i] = v;
  }
  cur.copy(out, y * stride); prev = cur;
}
const px = (x, y) => { const i = y * stride + x * channels; return [out[i], out[i + 1], out[i + 2]]; };
const pts = [[1350,440],[1400,440],[1420,440],[1435,440],[1400,425],[1400,460],[1350,500],[1430,500],[1400,700],[300,440]];
console.log(JSON.stringify({ width, height, colorType, samples: pts.map(([x,y]) => ({ x, y, rgb: px(x, y) })) }));
