// 添加水印：文字直接写成矢量字（中文按需下载字体并子集嵌入，体积只增加几 KB），图片按 PNG/JPG 嵌入。
// 移植自 BentoPDF（AGPL-3.0）src/js/utils/pdf-operations.ts（addTextWatermark、addImageWatermark、computeTileCenters），
// 按本站引擎与界面重写：BentoPDF 把文字画到 canvas 再嵌成图片，字形随系统字体而变；它还忽略页面的 /Rotate 和 CropBox，
// 横放或裁过边的页面上水印会偏到别处。这里按读者实际看到的页面摆放。
import type { PDFDocument, PDFImage, PDFOperator, PDFPage } from '@cantoo/pdf-lib';
import type { Progress } from '../engines.svelte';
import { type CjkFont, fontForText, loadPdfLib, openPdfLib, ownResources, savePdfLib } from '../engines/pdflib';
import { expandRanges } from '../ranges';

type PdfLib = Awaited<ReturnType<typeof loadPdfLib>>;

interface Common {
  /** 本站页码写法（"1-3,5,8-"），空为全部页 */
  pages: string;
  /** 不透明度 0–1 */
  opacity: number;
  /** 旋转角度，读者看到的逆时针方向 */
  angle: number;
  /** 铺满整页；否则放在页面正中 */
  tile: boolean;
}

export interface TextMark extends Common {
  kind: 'text';
  text: string;
  font: CjkFont;
  size: number;
  /** #rrggbb */
  color: string;
}

export interface ImageMark extends Common {
  kind: 'image';
  image: Uint8Array;
  /** 图片宽度占页面宽度的比例 */
  width: number;
}

export type Watermark = TextMark | ImageMark;

export interface WatermarkHooks {
  /** 中文字体的下载进度 */
  onFont?: (p: Progress) => void;
  onPage?: (done: number, total: number) => void;
}

export interface WatermarkResult {
  bytes: Uint8Array;
  /** 字体里没有、会显示为空白的字符 */
  missing: string[];
  /** 加了水印的页（0 起计） */
  pages: number[];
}

/** 读者看到的页面：CropBox 按 /Rotate 转正后的宽高，原点在左下；toUser 换算回页面自身的坐标 */
interface Frame {
  w: number;
  h: number;
  rot: number;
  toUser(vx: number, vy: number): { x: number; y: number };
}

function frameOf(page: PDFPage): Frame {
  const { x: x0, y: y0, width: W, height: H } = page.getCropBox();
  const rot = (((Math.round(page.getRotation().angle / 90) * 90) % 360) + 360) % 360;
  switch (rot) {
    case 90:
      return { w: H, h: W, rot, toUser: (vx, vy) => ({ x: x0 + W - vy, y: y0 + vx }) };
    case 180:
      return { w: W, h: H, rot, toUser: (vx, vy) => ({ x: x0 + W - vx, y: y0 + H - vy }) };
    case 270:
      return { w: H, h: W, rot, toUser: (vx, vy) => ({ x: x0 + vy, y: y0 + H - vx }) };
    default:
      return { w: W, h: H, rot: 0, toUser: (vx, vy) => ({ x: x0 + vx, y: y0 + vy }) };
  }
}

/** 中心在 (cx, cy)、相对中心偏移 (lx, ly) 的绘制起点，整体旋转 deg 度；返回页面坐标与页面坐标下的角度 */
function originFor(f: Frame, cx: number, cy: number, lx: number, ly: number, deg: number) {
  const a = (deg * Math.PI) / 180;
  const vx = cx + lx * Math.cos(a) - ly * Math.sin(a);
  const vy = cy + lx * Math.sin(a) + ly * Math.cos(a);
  return { ...f.toUser(vx, vy), angle: deg + f.rot };
}

const MAX_TILES = 1500;

/** 平铺时各个水印的中心：沿水印方向排成网格，只保留和页面有交集的 */
function tileCenters(pageW: number, pageH: number, itemW: number, itemH: number, angle: number, gapX: number, gapY: number) {
  if (!(pageW > 0 && pageH > 0 && itemW > 0 && itemH > 0)) return [];
  const rad = (angle * Math.PI) / 180;
  const [ux, uy, vx, vy] = [Math.cos(rad), Math.sin(rad), -Math.sin(rad), Math.cos(rad)];
  let stepX = Math.max(itemW * (1 + gapX), 1);
  let stepY = Math.max(itemH * (1 + gapY), 1);
  // 字很小时限制数量，免得一页画上万个
  const density = (pageW * pageH) / (stepX * stepY);
  if (density > MAX_TILES) {
    const k = Math.sqrt(density / MAX_TILES);
    stepX *= k;
    stepY *= k;
  }
  const reach = Math.hypot(pageW, pageH) / 2 + Math.hypot(itemW, itemH) / 2;
  const hx = (Math.abs(ux) * itemW + Math.abs(vx) * itemH) / 2;
  const hy = (Math.abs(uy) * itemW + Math.abs(vy) * itemH) / 2;
  const iMax = Math.ceil(reach / stepX);
  const jMax = Math.ceil(reach / stepY);
  const out: { x: number; y: number }[] = [];
  for (let j = -jMax; j <= jMax; j++) {
    for (let i = -iMax; i <= iMax; i++) {
      const x = pageW / 2 + i * stepX * ux + j * stepY * vx;
      const y = pageH / 2 + i * stepX * uy + j * stepY * vy;
      if (x + hx < 0 || x - hx > pageW || y + hy < 0 || y - hy > pageH) continue;
      out.push({ x, y });
    }
  }
  return out;
}

/** 去掉控制字符（Helvetica 写不了制表符等）和变体选择符、零宽连接符这类不可见字符，首尾空行不占位置 */
export function watermarkLines(text: string) {
  const lines = text
    .replace(/\r\n?/g, '\n')
    .replaceAll('\t', '    ')
    .replace(/[\u0000-\u0009\u000b-\u001f\u007f-\u009f]|\p{Default_Ignorable_Code_Point}/gu, '')
    .split('\n');
  while (lines.length && !lines[0].trim()) lines.shift();
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return lines;
}

async function openForEdit(bytes: Uint8Array) {
  try {
    const doc = await openPdfLib(bytes);
    if (doc.getPageCount() > 0) return doc;
  } catch {
    /* 交给 qpdf 修复后再试 */
  }
  // pdf-lib 读不了的文件（交叉引用表损坏等）先让 qpdf 重写一遍
  try {
    const { qpdfOne } = await import('../engines/qpdf');
    const doc = await openPdfLib(await qpdfOne([], bytes));
    if (doc.getPageCount() > 0) return doc;
  } catch {
    /* 下面统一报错 */
  }
  throw new Error('无法读取这个 PDF，文件可能已损坏');
}

async function embedImage(doc: PDFDocument, bytes: Uint8Array): Promise<PDFImage> {
  const png = bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47;
  const jpg = bytes[0] === 0xff && bytes[1] === 0xd8;
  try {
    if (png) return await doc.embedPng(bytes);
    if (jpg) return await doc.embedJpg(bytes);
  } catch {
    throw new Error('水印图片无法读取，可能已损坏，请换一张 PNG 或 JPG');
  }
  // WebP 等格式由浏览器解码后转成 PNG
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(new Blob([bytes as BlobPart]));
  } catch {
    throw new Error('无法识别水印图片的格式，请换用 PNG 或 JPG');
  }
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  canvas.getContext('2d')!.drawImage(bitmap, 0, 0);
  bitmap.close();
  const blob = await canvas.convertToBlob({ type: 'image/png' });
  return doc.embedPng(new Uint8Array(await blob.arrayBuffer()));
}

/** "#rrggbb" → 0–1 的 RGB 分量 */
function hexColor(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) throw new Error(`颜色写法有误：${hex}`);
  return [m[1], m[2], m[3]].map((c) => round(parseInt(c, 16) / 255, 4)) as [number, number, number];
}

/** pdf-lib 按完整精度写数字（cos 90° 会写成一长串 0.0000…），平铺时每个水印都要写一遍，先舍入 */
function round(v: number, digits: number) {
  const k = 10 ** digits;
  return Math.round(v * k) / k || 0;
}

/** 给未加密的 PDF 加水印（加密文件先用 input.ts 的 unlockPdf 解开） */
export async function addWatermark(input: Uint8Array, mark: Watermark, hooks: WatermarkHooks = {}): Promise<WatermarkResult> {
  const lib = await loadPdfLib();
  const { beginText, endText, popGraphicsState, pushGraphicsState, setFillingColor, setFontAndSize, setGraphicsState, setTextMatrix, showText } = lib;
  const { concatTransformationMatrix, drawObject } = lib;
  const doc = await openForEdit(input);
  const ctx = doc.context;
  const pages = doc.getPages();
  const targets = mark.pages.trim() ? expandRanges(mark.pages, pages.length).sort((a, b) => a - b) : [...pages.keys()];
  // 所有页共用一个透明度对象，每页只加一个引用
  const gsRef = ctx.register(ctx.obj({ Type: 'ExtGState', ca: round(Math.min(1, Math.max(0, mark.opacity)), 3) }));
  let missing: string[] = [];
  let size: (f: Frame) => { w: number; h: number };
  // 一页上所有水印共用一个字体键（或图片键），只写各自的位置
  let paint: (page: PDFPage, f: Frame, centers: { x: number; y: number }[]) => PDFOperator[];
  // 平铺间距（相对水印自身的宽高）；比 BentoPDF 的 0.25/0.75 稀一些，正文不至于被盖满
  let gap = { x: 0.5, y: 1.5 };

  if (mark.kind === 'text') {
    const lines = watermarkLines(mark.text);
    if (!lines.length) throw new Error('请输入水印文字');
    const { font, missing: absent } = await fontForText(doc, lines.join('\n'), mark.font, hooks.onFont);
    missing = absent;
    const fontSize = mark.size;
    const lineHeight = fontSize * 1.2;
    const ascent = font.heightAtSize(fontSize, { descender: false });
    const descent = font.heightAtSize(fontSize) - ascent;
    // 基线到一行字视觉中心的距离
    const mid = (ascent - descent) / 2;
    const widths = lines.map((line) => font.widthOfTextAtSize(line, fontSize));
    const box = { w: Math.max(...widths), h: lines.length * lineHeight };
    const color = lib.rgb(...hexColor(mark.color));
    // 编码时会记下子集要保留的字形，每行编码一次即可
    const encoded = lines.map((line) => (line.trim() ? font.encodeText(line) : null));
    size = () => box;
    paint = (page, f, centers) => {
      const key = page.node.newFontDictionary(font.name, font.ref);
      const ops = [beginText(), setFillingColor(color), setFontAndSize(key, fontSize)];
      for (const c of centers) {
        encoded.forEach((hex, i) => {
          if (!hex) return;
          const o = originFor(f, c.x, c.y, -widths[i] / 2, ((lines.length - 1) / 2 - i) * lineHeight - mid, mark.angle);
          const a = (o.angle * Math.PI) / 180;
          const [cos, sin] = [round(Math.cos(a), 5), round(Math.sin(a), 5)];
          ops.push(setTextMatrix(cos, sin, -sin, cos, round(o.x, 2), round(o.y, 2)), showText(hex));
        });
      }
      ops.push(endText());
      return ops;
    };
  } else {
    const image = await embedImage(doc, mark.image);
    const ratio = image.height / image.width;
    gap = { x: 0.5, y: 0.5 };
    size = (f) => ({ w: f.w * mark.width, h: f.w * mark.width * ratio });
    paint = (page, f, centers) => {
      const key = page.node.newXObject('Image', image.ref);
      const { w, h } = size(f);
      const ops: PDFOperator[] = [];
      for (const c of centers) {
        const o = originFor(f, c.x, c.y, -w / 2, -h / 2, mark.angle);
        const a = (o.angle * Math.PI) / 180;
        const [cos, sin] = [Math.cos(a), Math.sin(a)];
        ops.push(
          pushGraphicsState(),
          concatTransformationMatrix(round(w * cos, 3), round(w * sin, 3), round(-h * sin, 3), round(h * cos, 3), round(o.x, 2), round(o.y, 2)),
          drawObject(key),
          popGraphicsState(),
        );
      }
      return ops;
    };
  }

  for (const [n, index] of targets.entries()) {
    const page = pages[index];
    const f = frameOf(page);
    const { w, h } = size(f);
    const centers = mark.tile ? tileCenters(f.w, f.h, w, h, mark.angle, gap.x, gap.y) : [{ x: f.w / 2, y: f.h / 2 }];
    ownResources(lib, page);
    const gs = page.node.newExtGState('GS', gsRef);
    const ops = [pushGraphicsState(), setGraphicsState(gs), ...paint(page, f, centers), popGraphicsState()];
    // 分批传参，一页上千个水印时不至于超出函数参数个数上限
    for (let i = 0; i < ops.length; i += 2000) page.pushOperators(...ops.slice(i, i + 2000));
    // 绘制是同步的，隔几页让出主线程，进度条才会动
    if (n % 10 === 9) {
      hooks.onPage?.(n + 1, targets.length);
      await new Promise((r) => setTimeout(r));
    }
  }
  hooks.onPage?.(targets.length, targets.length);
  return { bytes: await savePdfLib(doc), missing, pages: targets };
}
