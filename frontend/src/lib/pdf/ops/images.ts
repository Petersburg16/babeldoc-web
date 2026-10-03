// PDF 与图片互转。
// 移植自 BentoPDF（AGPL-3.0）src/js/logic/pdf-to-jpg-page.ts、pdf-to-png-page.ts、image-to-pdf-page.ts、
// src/js/utils/images-to-pdf-lib.ts，按本站引擎与界面重写。
// 两个工具共用这个模块，所以 pdf.js 和 pdf-lib 都在函数里按需加载：图片转 PDF 不必下载 pdf.js，反之亦然。
import type { PDFImage } from '@cantoo/pdf-lib';
import { type OutputFile, type Report, readBytes, stem } from '../files';
import { expandRanges } from '../ranges';

// ===== PDF → 图片 =====

export type ImageFormat = 'png' | 'jpeg';

export interface ToImagesOptions {
  format: ImageFormat;
  dpi: number;
  /** JPG 质量 0–1 */
  quality: number;
  /** 本站页码写法，空表示全部 */
  pages: string;
}

export interface ToImagesResult {
  outputs: OutputFile[];
  /** 每张图的像素尺寸 */
  sizes: [number, number][];
  /** 超过浏览器画布上限、被降低了分辨率的页（1 起计） */
  reduced: number[];
}

const MIME = { png: 'image/png', jpeg: 'image/jpeg' } as const;

/** 逐页渲染导出（bytes 需未加密）；一次只占用一张画布，导出后立即释放 */
export async function pdfToImages(bytes: Uint8Array, name: string, o: ToImagesOptions, report: Report): Promise<ToImagesResult> {
  const { openPdf, closePdf, renderPage, canvasToBlob, releaseCanvas } = await import('../engines/pdfjs');
  const doc = await openPdf(bytes);
  try {
    const indices = o.pages.trim() ? expandRanges(o.pages, doc.numPages) : [...Array(doc.numPages).keys()];
    const digits = Math.max(2, String(doc.numPages).length);
    const ext = o.format === 'jpeg' ? 'jpg' : 'png';
    const result: ToImagesResult = { outputs: [], sizes: [], reduced: [] };
    for (const [k, index] of indices.entries()) {
      report(k / indices.length, `正在导出第 ${index + 1} 页（${k + 1}/${indices.length}）`);
      const wanted = (await doc.getPage(index + 1)).getViewport({ scale: o.dpi / 72 });
      const canvas = await renderPage(doc, index, { dpi: o.dpi });
      try {
        if (canvas.width * canvas.height < Math.floor(wanted.width) * Math.floor(wanted.height) * 0.98) {
          result.reduced.push(index + 1);
        }
        const blob = await canvasToBlob(canvas, MIME[o.format], o.format === 'jpeg' ? o.quality : undefined);
        result.outputs.push({ name: `${stem(name)}-第${String(index + 1).padStart(digits, '0')}页.${ext}`, blob });
        result.sizes.push([canvas.width, canvas.height]);
      } finally {
        releaseCanvas(canvas);
      }
    }
    report(1);
    return result;
  } finally {
    await closePdf(doc);
  }
}

// ===== 图片 → PDF =====

export type PaperSize = 'fit' | 'A4' | 'Letter';
export type Orientation = 'auto' | 'portrait' | 'landscape';

export interface ImagesToPdfOptions {
  /** fit = 页面跟随图片大小 */
  paper: PaperSize;
  /** 只对固定纸张有效；auto 时横图用横向页面 */
  orientation: Orientation;
  marginMm: number;
}

const PAPERS = { A4: [595.28, 841.89], Letter: [612, 792] } as const;
const MM = 72 / 25.4;
// 图片没写分辨率（或写的是相机默认的 72 dpi）时，“跟随图片”按这个分辨率换算页面尺寸
const DEFAULT_DPI = 150;
// PDF 阅读器普遍支持的最大页面边长（200 英寸）
const MAX_PAGE_PT = 14_400;
// 画布像素上限，与 engines/pdfjs.ts 一致（这里不 import 它，免得图片转 PDF 也加载 pdf.js）
const MAX_PIXELS = /Android|iPhone|iPad/i.test(navigator.userAgent) ? 5_242_880 : 33_554_432;

/** JPEG 的 EXIF 方向（0x0112），没有时为 1 */
export function jpegOrientation(b: Uint8Array): number {
  if (b[0] !== 0xff || b[1] !== 0xd8) return 1;
  for (let i = 2; i + 9 < b.length; ) {
    if (b[i] !== 0xff) return 1;
    const marker = b[i + 1];
    const len = (b[i + 2] << 8) | b[i + 3];
    if (marker === 0xda) return 1; // 图像数据开始，后面不会再有 EXIF
    if (marker === 0xe1 && b[i + 4] === 0x45 && b[i + 5] === 0x78 && b[i + 6] === 0x69 && b[i + 7] === 0x66) {
      const t = i + 10;
      const le = b[t] === 0x49; // "II" 小端，"MM" 大端
      const u16 = (o: number) => (le ? b[o] | (b[o + 1] << 8) : (b[o] << 8) | b[o + 1]);
      const u32 = (o: number) =>
        le ? (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)) + b[o + 3] * 2 ** 24 : b[o] * 2 ** 24 + ((b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]);
      const ifd = t + u32(t + 4);
      for (let k = 0, n = u16(ifd); k < n && ifd + 14 + k * 12 <= b.length; k++) {
        const e = ifd + 2 + k * 12;
        if (u16(e) === 0x0112) {
          const v = u16(e + 8);
          return v >= 1 && v <= 8 ? v : 1;
        }
      }
      return 1;
    }
    i += 2 + len;
  }
  return 1;
}

/** JPEG 结束标记（EOI）之后的位置；数据不完整（如没下载完）时返回 -1 */
export function jpegEnd(b: Uint8Array): number {
  for (let i = 2; i + 1 < b.length; ) {
    // 段与段之间的多余字节：libjpeg 会跳过，这里也跳过
    if (b[i] !== 0xff || b[i + 1] === 0) {
      i++;
      continue;
    }
    const marker = b[i + 1];
    if (marker === 0xd9) return i + 2;
    // 填充字节和不带长度的标记
    if (marker === 0xff) i += 1;
    else if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) i += 2;
    else {
      if (i + 3 >= b.length) return -1;
      i += 2 + ((b[i + 2] << 8) | b[i + 3]);
      // 按段长度跳过，不能直接找 FFD9：EXIF 里的缩略图有自己的结束标记。
      // 熵编码数据里 0xFF 后面只会是 0x00 或 RSTn，遇到别的就是下一个标记（渐进式 JPEG 有多段扫描）
      if (marker === 0xda) {
        while (i + 1 < b.length && !(b[i] === 0xff && b[i + 1] !== 0 && (b[i + 1] < 0xd0 || b[i + 1] > 0xd7))) i++;
      }
    }
  }
  return -1;
}

/** 图片自带的分辨率（JPEG 的 JFIF、PNG 的 pHYs）；扫描件一般会写，没有或不可信时返回 null */
export function imageDpi(b: Uint8Array): number | null {
  let dpi = 0;
  if (b[0] === 0xff && b[1] === 0xd8) {
    // JFIF 段紧跟在 SOI 之后：单位 1 = 每英寸，2 = 每厘米
    if (b[2] === 0xff && b[3] === 0xe0 && b[6] === 0x4a && b[7] === 0x46 && b[8] === 0x49 && b[9] === 0x46) {
      const density = (b[14] << 8) | b[15];
      dpi = b[13] === 1 ? density : b[13] === 2 ? density * 2.54 : 0;
    }
  } else if (b[0] === 0x89 && b[1] === 0x50) {
    const view = new DataView(b.buffer, b.byteOffset, b.byteLength);
    for (let i = 8; i + 8 <= b.length; ) {
      const len = view.getUint32(i);
      const type = String.fromCharCode(b[i + 4], b[i + 5], b[i + 6], b[i + 7]);
      if (type === 'IDAT' || type === 'IEND') break;
      if (type === 'pHYs' && len >= 9 && i + 17 <= b.length) {
        if (b[i + 16] === 1) dpi = view.getUint32(i + 8) * 0.0254; // 单位 1 = 每米
        break;
      }
      i += 12 + len;
    }
  }
  dpi = Math.round(dpi);
  return dpi >= 96 && dpi <= 2400 ? dpi : null;
}

function layout(w: number, h: number, dpi: number, o: ImagesToPdfOptions) {
  const natW = (w * 72) / dpi;
  const natH = (h * 72) / dpi;
  const m = o.marginMm * MM;
  let pw: number;
  let ph: number;
  if (o.paper === 'fit') {
    // 超大图按阅读器的页面上限等比缩小
    const s = Math.min(1, MAX_PAGE_PT / (Math.max(natW, natH) + 2 * m));
    pw = natW * s + 2 * m;
    ph = natH * s + 2 * m;
  } else {
    [pw, ph] = PAPERS[o.paper];
    const landscape = o.orientation === 'landscape' || (o.orientation === 'auto' && w > h);
    if (landscape) [pw, ph] = [ph, pw];
  }
  const bw = pw - 2 * m;
  const bh = ph - 2 * m;
  const s = Math.min(bw / natW, bh / natH);
  const dw = natW * s;
  const dh = natH * s;
  return { pw, ph, x: m + (bw - dw) / 2, y: m + (bh - dh) / 2, dw, dh };
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality?: number) {
  return new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('图片转换失败，可能是图片太大'))), type, quality),
  );
}

/** 按条带抽查是否有透明像素，避免一次取出整张图的像素 */
function hasAlpha(ctx: CanvasRenderingContext2D, width: number, height: number) {
  const strip = Math.max(1, Math.floor(4_000_000 / width));
  for (let y = 0; y < height; y += strip) {
    const data = ctx.getImageData(0, y, width, Math.min(strip, height - y)).data;
    for (let i = 3; i < data.length; i += 4) if (data[i] < 255) return true;
  }
  return false;
}

/** 浏览器能解码的任何图片（WebP、GIF、BMP、AVIF、镜像方向的 JPEG…）→ 有透明时 PNG，否则 JPEG */
async function rasterize(
  file: Blob,
  mayHaveAlpha: boolean,
  failure: string,
): Promise<{ bytes: Uint8Array; png: boolean; scale: number }> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  } catch {
    throw new Error(failure);
  }
  const scale = Math.min(1, Math.sqrt(MAX_PIXELS / (bitmap.width * bitmap.height)));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  try {
    const ctx = canvas.getContext('2d', { willReadFrequently: mayHaveAlpha })!;
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const png = mayHaveAlpha && hasAlpha(ctx, canvas.width, canvas.height);
    const blob = await toBlob(canvas, png ? 'image/png' : 'image/jpeg', 0.92);
    return { bytes: await readBytes(blob), png, scale: canvas.width / bitmap.width };
  } finally {
    bitmap.close();
    canvas.width = canvas.height = 0;
  }
}

/** 常见但 Chrome、Edge 等解不开的格式（按扩展名或文件头），解码失败时给出具体提示 */
function formatHint(name: string, b: Uint8Array) {
  const brand = String.fromCharCode(...b.subarray(4, 12));
  if (/\.hei[cf]$/i.test(name) || /^ftyp(he[iv][cxms]|m[is]f1)$/.test(brand)) return 'HEIC';
  const tiff = (b[0] === 0x49 && b[1] === 0x49 && b[2] === 0x2a && b[3] === 0) || (b[0] === 0x4d && b[1] === 0x4d && b[2] === 0 && b[3] === 0x2a);
  if (/\.tiff?$/i.test(name) || tiff) return 'TIFF';
  return '';
}

/** 每张图片一页，按 files 的顺序；返回 PDF 字节 */
export async function imagesToPdf(files: File[], o: ImagesToPdfOptions, report: Report): Promise<Uint8Array> {
  const { loadPdfLib, savePdfLib } = await import('../engines/pdflib');
  const { PDFDocument, degrees } = await loadPdfLib();
  // 不写 pdf-lib 的 Producer 等元数据
  const doc = await PDFDocument.create({ updateMetadata: false });
  for (const [i, file] of files.entries()) {
    report(i / files.length, `正在处理「${file.name}」（${i + 1}/${files.length}）`);
    try {
      const bytes = await readBytes(file);
      const isJpeg = bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
      const isPng = bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47;
      let img: PDFImage | null = null;
      let ori = 1;
      let dpi = imageDpi(bytes) ?? DEFAULT_DPI;
      const hint = formatHint(file.name, bytes);
      let failure = hint ? `无法读取：浏览器不支持 ${hint} 图片，请先转成 JPG 或 PNG` : '无法读取：文件已损坏，或浏览器不支持这种图片格式';
      // 按文件头而不是扩展名判断格式：微信等保存的 .png 常常其实是 JPEG
      if (isJpeg) {
        ori = jpegOrientation(bytes);
        const end = jpegEnd(bytes);
        // pdf-lib 只读文件头，残缺的 JPEG 会被原样写进 PDF；交给浏览器解码，解不开就报错
        if (end < 0) failure = '无法读取：图片数据不完整，文件可能没下载完或已损坏';
        // JPEG 原样嵌入不重新压缩；pdf-lib 不看 EXIF 方向，旋转在绘制时处理，镜像方向只能先转一次。
        // 结束标记之后的数据（动态照片附带的视频等）不需要
        else if (![2, 4, 5, 7].includes(ori)) img = await doc.embedJpg(end < bytes.length ? bytes.slice(0, end) : bytes).catch(() => null);
      } else if (isPng) {
        img = await doc.embedPng(bytes).catch(() => null);
      }
      if (!img) {
        ori = 1;
        const r = await rasterize(file, !isJpeg, failure);
        img = r.png ? await doc.embedPng(r.bytes) : await doc.embedJpg(r.bytes);
        dpi *= r.scale; // 超大图转换时缩小过，页面尺寸仍按原图算
      }
      // 方向 5–8 存的是横图、显示为竖图
      const swap = ori >= 5;
      const L = layout(swap ? img.height : img.width, swap ? img.width : img.height, dpi, o);
      const page = doc.addPage([L.pw, L.ph]);
      const { x, y, dw, dh } = L;
      // drawImage 绕 (x, y) 旋转，宽高按图片存储方向给
      if (ori === 6) page.drawImage(img, { x, y: y + dh, width: dh, height: dw, rotate: degrees(-90) });
      else if (ori === 8) page.drawImage(img, { x: x + dw, y, width: dh, height: dw, rotate: degrees(90) });
      else if (ori === 3) page.drawImage(img, { x: x + dw, y: y + dh, width: dw, height: dh, rotate: degrees(180) });
      else page.drawImage(img, { x, y, width: dw, height: dh });
    } catch (e) {
      throw new Error(`「${file.name}」${e instanceof Error ? e.message : String(e)}`);
    }
  }
  report(null, '正在生成 PDF');
  return savePdfLib(doc);
}
