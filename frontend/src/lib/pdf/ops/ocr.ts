// OCR：扫描件 → 可搜索、可复制的 PDF，全程在浏览器里完成。
// PDF.js 把页面画成灰度图，tesseract.js（WASM，跑在它自己的 worker 里）识别，并用 Tesseract 自带的 PDF 渲染器
// 生成一页只有不可见文字的 PDF；再用 pdf-lib 把这层文字原位叠到原页面上。原文件的书签、元数据、表单、链接都保留，
// 图像也不重新压缩。文字层用 Tesseract 内置的 GlyphLessFont（几百字节），中文不用另外下载字体。
// 移植自 BentoPDF（AGPL-3.0）src/js/utils/ocr.ts，按本站引擎与界面重写：BentoPDF 用真字体逐词 drawText 到新文档，
// 会丢书签和元数据，也不处理 /Rotate 和 CropBox。
import {
  decodePDFRawStream,
  degrees,
  PDFArray,
  PDFDocument,
  PDFName,
  PDFRawStream,
  PDFStream,
  type PDFEmbeddedPage,
  type PDFPage,
} from '@cantoo/pdf-lib';
import { createWorker, type LoggerMessage, OEM, PSM, type Worker as TessWorker } from 'tesseract.js';
import { errorText } from '../../format';
import { assetBase, assetUrl } from '../engines.svelte';
import { closePdf, openPdf, type PdfDoc, pdfjs, releaseCanvas, renderPage } from '../engines/pdfjs';
import { openPdfLib, savePdfLib } from '../engines/pdflib';
import { qpdfOne } from '../engines/qpdf';
import type { Report } from '../files';
import { Cancelled } from '../input';
import { expandRanges } from '../ranges';

/** chi_sim 必须排在前面：反过来时中文识别明显变差（“风光”认成“凡 >”） */
export type OcrLanguage = 'chi_sim+eng' | 'eng';

export interface OcrOptions {
  language: OcrLanguage;
  dpi: number;
  /** 页码范围（本站写法），空为全部页 */
  range?: string;
  /** 跳过本来就有文字的页，免得叠出两层文字 */
  skipText: boolean;
  signal?: AbortSignal;
  report: Report;
}

export interface OcrResult {
  pdf: Uint8Array;
  /** 按页拼好的纯文本（跳过的页取原有文字） */
  text: string;
  /** 一个字也没有（识别和跳过的页都是空的） */
  empty: boolean;
  recognized: number;
  skipped: number;
  failed: { page: number; reason: string }[];
  /** 平均置信度很低的页（多半是页面横着、倒着或很模糊），1 起计 */
  doubtful: number[];
}

// 单页像素上限：A4 300 dpi 约 870 万像素；再大的页面降分辨率，免得 WASM 内存吃紧
const MAX_PIXELS = 12_000_000;
// 至少这么多个字才算“已有文字”，扫描软件加的一行页眉不算
const TEXT_MIN_CHARS = 20;
// 整页是一张图（占页面一半以上）、文字又只占很小一块时，仍当扫描件：文字多半是页脚或下载水印。
// 一行 IEEE 下载页脚约占页面 1%，识别过的扫描页满页文字在 30% 以上
const SPARSE_TEXT = 0.03;
const FULL_PAGE_IMAGE = 0.5;
// tesseract.js 的任务没法取消，worker 卡住（例如 WASM 内存不足崩溃）时只能靠超时发现，再换一个 worker
const PAGE_TIMEOUT_MS = 240_000;
const SPAWN_TIMEOUT_MS = 120_000;
// 正常扫描页的平均置信度在 85–95，横着或倒着的页只有 30 左右
const LOW_CONFIDENCE = 60;

const STATUS: Record<string, string> = {
  'loading tesseract core': '正在加载识别引擎',
  'initializing tesseract': '正在加载识别引擎',
  'loading language traineddata': '正在加载语言包',
  'initializing api': '正在初始化识别引擎',
};

class Timeout extends Error {
  constructor() {
    super('识别超时');
  }
}

function abortable<T>(job: Promise<T>, ms: number, signal?: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Timeout()), ms);
    const onAbort = () => reject(new Cancelled());
    if (signal?.aborted) onAbort();
    signal?.addEventListener('abort', onAbort, { once: true });
    job.then(resolve, reject).finally(() => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
    });
  });
}

/** tesseract.js 抛出的多是字符串 */
const reason = (e: unknown) => errorText(e).replace(/^Error:\s*/, '');

/**
 * createWorker 在第一个 await 之前就同步 new Worker()，但要等引擎和语言包都加载好才把它交出来；
 * 中途取消、超时或加载失败时拿不到 terminate，线程会一直占着几十 MB 内存直到刷新页面。
 * 所以在这一小段同步调用里临时包一层 Worker 构造函数，把建出来的线程记下来（tesseract.js 版本已锁定）。
 */
function captureThreads<T>(start: () => T) {
  const Native = globalThis.Worker;
  const threads: Worker[] = [];
  globalThis.Worker = class extends Native {
    constructor(...args: ConstructorParameters<typeof Worker>) {
      super(...args);
      threads.push(this);
    }
  };
  try {
    return { result: start(), threads };
  } finally {
    globalThis.Worker = Native;
  }
}

/**
 * 启动识别 worker。所有地址都指向本站 /pdf-assets/ocr/，覆盖 tesseract.js 默认的 jsDelivr。
 * 语言包加载失败时 createWorker 返回的 promise 永远不结束，只会调用 errorHandler，所以要自己接住。
 */
async function spawnWorker(language: OcrLanguage, logger: (m: LoggerMessage) => void, signal?: AbortSignal) {
  const base = assetBase('ocr');
  let fail: (e: unknown) => void = () => {};
  const failed = new Promise<never>((_, reject) => (fail = reject));
  const { result: created, threads } = captureThreads(() =>
    createWorker(language === 'eng' ? ['eng'] : ['chi_sim', 'eng'], OEM.LSTM_ONLY, {
      workerPath: assetUrl('ocr', 'worker.min.js'),
      // 目录：worker 按浏览器能力在 relaxedsimd / simd / 普通三个核心里挑一个
      corePath: base + 'core',
      langPath: base + 'lang',
      gzip: true,
      // 语言包已在 Cache Storage 里，不再往 IndexedDB 存一份解压后的副本
      cacheMethod: 'none',
      workerBlobURL: false,
      logger,
      errorHandler: (e: unknown) => fail(e),
    }),
  );
  try {
    const worker = await abortable(Promise.race([created, failed]), SPAWN_TIMEOUT_MS, signal);
    await worker.setParameters({
      // tesseract.js 默认按单个文本块识别，整页要用自动分版
      tessedit_pageseg_mode: PSM.AUTO,
      // 纯文本里中文词之间不插空格，文字层的空格也照它来定
      preserve_interword_spaces: '1',
    });
    return worker;
  } catch (e) {
    // 没启动完就放弃了：结束线程；万一没记到线程，等它启动完再关
    for (const thread of threads) thread.terminate();
    created.then((w) => w.terminate(), () => {});
    if (e instanceof Cancelled) throw e;
    console.error('[ocr] 引擎启动失败', e);
    throw new Error('识别引擎加载失败，请检查网络后重试');
  }
}

// ---------------------------------------------------------------- 渲染

/** 渲染成灰度 PGM：Tesseract 自带的 Leptonica 能直接读，省掉 PNG 编码和解码 */
async function renderGray(doc: PdfDoc, index: number, dpi: number) {
  const page = await doc.getPage(index + 1);
  const { width, height } = page.getViewport({ scale: 1 });
  const capped = Math.min(dpi, 72 * Math.sqrt(MAX_PIXELS / (width * height)));
  const canvas = await renderPage(doc, index, { dpi: capped });
  try {
    const w = canvas.width;
    const h = canvas.height;
    const { data } = canvas.getContext('2d')!.getImageData(0, 0, w, h);
    const header = new TextEncoder().encode(`P5\n${w} ${h}\n255\n`);
    const pgm = new Uint8Array(header.length + w * h);
    pgm.set(header);
    for (let i = 0, j = header.length; i < data.length; i += 4, j++) {
      pgm[j] = (data[i] * 77 + data[i + 1] * 150 + data[i + 2] * 29) >> 8;
    }
    // 画布不带分辨率信息，要告诉 Tesseract 实际 dpi（PDF.js 可能因像素上限又缩小过）
    return { pgm, dpi: Math.round((w / width) * 72) };
  } finally {
    releaseCanvas(canvas);
  }
}

/** 页面上已有的文字；没有可搜索的正文（空白页、只有一行页脚的扫描页）时返回 null */
async function existingText(doc: PdfDoc, index: number) {
  const page = await doc.getPage(index + 1);
  try {
    const content = await page.getTextContent();
    let text = '';
    let area = 0;
    for (const item of content.items) {
      if (!('str' in item)) continue;
      text += item.str + (item.hasEOL ? '\n' : '');
      if (item.str.trim()) area += Math.abs(item.width * item.height);
    }
    if (text.replace(/\s+/g, '').length < TEXT_MIN_CHARS) return null;
    // 扫描页上加的下载页脚、图书馆水印也是真文字，不能因此把整页当成已可搜索。
    // 只在文字稀少时才查图片：取操作列表时 PDF.js 会解码图片，比较慢
    const [x0, y0, x1, y1] = page.view;
    const pageArea = Math.abs((x1 - x0) * (y1 - y0));
    if (area < pageArea * SPARSE_TEXT && (await largestImage(page)) >= pageArea * FULL_PAGE_IMAGE) return null;
    return text.trim();
  } finally {
    page.cleanup();
  }
}

/** 页面上最大一张图片占的面积（PDF 单位）：图片画在单位正方形里，面积就是当时变换矩阵的行列式 */
async function largestImage(page: pdfjs.PDFPageProxy) {
  const { OPS } = pdfjs;
  const images = new Set<number>([OPS.paintImageXObject, OPS.paintInlineImageXObject, OPS.paintImageMaskXObject]);
  const list = await page.getOperatorList({ annotationMode: pdfjs.AnnotationMode.DISABLE });
  const det = (m: number[]) => m[0] * m[3] - m[1] * m[2];
  const stack: number[] = [];
  let scale = 1;
  let largest = 0;
  list.fnArray.forEach((fn, i) => {
    const args = list.argsArray[i];
    if (fn === OPS.save) stack.push(scale);
    else if (fn === OPS.restore) scale = stack.pop() ?? scale;
    else if (fn === OPS.transform) scale *= det(args);
    else if (fn === OPS.paintFormXObjectBegin) {
      stack.push(scale);
      if (Array.isArray(args?.[0])) scale *= det(args[0]);
    } else if (fn === OPS.paintFormXObjectEnd) scale = stack.pop() ?? scale;
    else if (images.has(fn)) largest = Math.max(largest, Math.abs(scale));
  });
  return largest;
}

// ---------------------------------------------------------------- 文字层

// 前后都不用空格的字符：汉字、假名、注音、CJK 标点、全角形式，以及中文里成对使用的破折号和省略号
const WIDE =
  /[\u2014\u2015\u2026\u2E80-\u2FDF\u3000-\u303F\u3040-\u30FF\u3100-\u312F\u3190-\u31FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\uFE10-\uFE1F\uFE30-\uFE4F\uFF00-\uFFEF\u{20000}-\u{3134F}]/u;
// 前引号后面、后引号前面不留空格，中英文都一样
const OPENING = /[\u2018\u201C]/;
const CLOSING = /[\u2019\u201D]/;

const hexToText = (hex: string) => {
  let s = '';
  for (let i = 0; i < hex.length; i += 4) s += String.fromCharCode(Number.parseInt(hex.slice(i, i + 4), 16));
  return s;
};

const toLatin1 = (bytes: Uint8Array) => {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return s;
};

const fromLatin1 = (s: string) => {
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 0xff;
  return out;
};

/** 识别文本对不上时的退路：按两侧的字符判断词间要不要空格 */
function spaceBetween(before: string, after: string) {
  const a = Array.from(before).pop() ?? '';
  const b = Array.from(after)[0] ?? '';
  return !(WIDE.test(a) || WIDE.test(b) || OPENING.test(a) || CLOSING.test(b));
}

/**
 * 识别文本（preserve_interword_spaces=1，即下载的 TXT）里每个词后面原本有没有空格：LSTM 只在看到真空格时才输出空格，
 * 所以“2026年10月3日”“第3章 风电”都和原文一致。文字层和文本出自同一个结果迭代器，词序相同；
 * 只有文本多出的非文字块、文字层略过的零宽词会错开，错开处往后找到这个词接上，那一处留 null。换行处也是 null。
 */
function spacesFromText(words: string[], text: string) {
  const spaces: (boolean | null)[] = words.map(() => null);
  let pos = 0;
  let linked = false;
  words.forEach((word, i) => {
    if (!word) {
      linked = false;
      return;
    }
    let p = pos;
    while (p < text.length && ' \t\r\n'.includes(text[p])) p++;
    if (text.startsWith(word, p)) {
      const gap = text.slice(pos, p);
      if (linked && !gap.includes('\n')) spaces[i - 1] = gap.length > 0;
      pos = p + word.length;
      linked = true;
    } else {
      const found = text.indexOf(word, pos);
      linked = found >= 0;
      if (linked) pos = found + word.length;
    }
  });
  return spaces;
}

// Tesseract 用 %g 输出数字，接近 0 的值会写成 -3.5527137e-15
const NUM = String.raw`[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?`;
const LAYER_OPS = new RegExp(String.raw`\bBT\b|/f-0-0 (${NUM}) Tf|(${NUM}) (${NUM}) Td|(${NUM}) Tz \[ <([0-9A-Fa-f]*)> \] TJ`, 'g');

interface LayerWord {
  start: number;
  end: number;
  tz: number;
  hex: string;
  size: number;
  block: number;
  /** 紧跟着的 Td：到下一个词起点的位移（文字空间单位，与 Tz 无关） */
  move: [number, number] | null;
}

/**
 * Tesseract 的 PDF 渲染器把每个词写成 `<Tz> Tz [ <UTF-16BE>0020 ] TJ`，再用 `<dx> <dy> Td` 移到下一个词。
 * 不分中西文，词后一律带空格，复制出来是“2026 年 10 月”；Tz 按词框宽度算，行首词的词框常偏宽两三倍、盖住后面的词，
 * MuPDF 就当成换行（“2026 \n年”）。这里按识别文本决定每个空格的去留，再把同一行每个词的 Tz 调到正好够到下一个词：
 * 提取文字的程序（PDF.js、MuPDF、pdfminer、PDFium）看到的字首尾相接，不会自己补空格或断行。
 * GlyphLessFont 的字宽固定 500，n 个字的宽度 = n × 0.5 × 字号 × Tz / 100。
 * 依赖 tesseract.js-core 7.0.0 的输出格式；匹配不上时原样保留。
 */
export function fixLayerSpacing(content: string, text: string) {
  const words: LayerWord[] = [];
  let size = 0;
  let block = 0;
  let last: LayerWord | null = null;
  for (const t of content.matchAll(LAYER_OPS)) {
    if (t[0] === 'BT') {
      block++;
      last = null;
    } else if (t[1] !== undefined) size = Number.parseFloat(t[1]);
    else if (t[2] !== undefined) {
      if (last && !last.move) last.move = [Number.parseFloat(t[2]), Number.parseFloat(t[3])];
    } else {
      last = { start: t.index, end: t.index + t[0].length, tz: Number.parseFloat(t[4]), hex: t[5], size, block, move: null };
      words.push(last);
    }
  }
  const trailing = words.map((w) => w.hex.length > 4 && /0020$/i.test(w.hex));
  const bodies = words.map((w, i) => hexToText(trailing[i] ? w.hex.slice(0, -4) : w.hex));
  const spaces = spacesFromText(bodies, text);

  let out = '';
  let cursor = 0;
  words.forEach((w, i) => {
    const body = bodies[i];
    if (!body || !(w.size > 0)) return;
    const next = words[i + 1];
    const after = next ? hexToText(next.hex) : '';
    const dx = w.move && next?.block === w.block && w.move[0] > 0 && Math.abs(w.move[1]) < w.size * 0.1 ? w.move[0] : 0;
    const keep = trailing[i] && ((dx ? spaces[i] : null) ?? spaceBetween(body, after));
    let tz = w.tz;
    if (dx) tz = (dx / ((body.length + (keep ? 1 : 0)) * 0.5 * w.size)) * 100;
    else if (trailing[i] && !keep) tz = (tz * (body.length + 1)) / body.length;
    out += content.slice(cursor, w.start) + `${+tz.toFixed(3)} Tz [ <${keep ? w.hex : w.hex.slice(0, body.length * 4)}> ] TJ`;
    cursor = w.end;
  });
  return out + content.slice(cursor);
}

/** 读入 Tesseract 生成的单页文字层，按识别文本改写内容流里的空格和字宽 */
async function loadLayer(bytes: Uint8Array, text: string): Promise<PDFPage> {
  const doc = await PDFDocument.load(bytes);
  const page = doc.getPage(0);
  const contents = page.node.Contents();
  if (!contents) return page;
  const streams = contents instanceof PDFArray ? contents.asArray().map((ref) => doc.context.lookup(ref)) : [contents];
  const content = streams
    .map((s) =>
      s instanceof PDFRawStream ? toLatin1(decodePDFRawStream(s).decode()) : s instanceof PDFStream ? toLatin1(s.getContents()) : '',
    )
    .join('\n');
  page.node.set(PDFName.of('Contents'), doc.context.register(doc.context.flateStream(fromLatin1(fixLayerSpacing(content, text)))));
  return page;
}

/** PDF.js 显示的区域：CropBox 与 MediaBox 的交集 */
function visibleBox(page: PDFPage) {
  const norm = (r: { x: number; y: number; width: number; height: number }) => ({
    x0: Math.min(r.x, r.x + r.width),
    y0: Math.min(r.y, r.y + r.height),
    x1: Math.max(r.x, r.x + r.width),
    y1: Math.max(r.y, r.y + r.height),
  });
  const m = norm(page.getMediaBox());
  const c = norm(page.getCropBox());
  const box = { x0: Math.max(m.x0, c.x0), y0: Math.max(m.y0, c.y0), x1: Math.min(m.x1, c.x1), y1: Math.min(m.y1, c.y1) };
  const use = box.x1 > box.x0 && box.y1 > box.y0 ? box : m;
  return { x: use.x0, y: use.y0, width: use.x1 - use.x0, height: use.y1 - use.y0 };
}

/**
 * 文字层是按显示方向（已转过 /Rotate、裁到 CropBox）识别的，画回页面时要反向转回页面坐标，
 * 否则旋转过或裁过边的扫描页文字会错位。
 */
function placeLayer(page: PDFPage, layer: PDFEmbeddedPage) {
  const box = visibleBox(page);
  const rot = (((page.getRotation().angle % 360) + 360) % 360) as 0 | 90 | 180 | 270;
  const width = rot % 180 === 0 ? box.width : box.height;
  const height = rot % 180 === 0 ? box.height : box.width;
  const origin = {
    0: [box.x, box.y],
    90: [box.x + box.width, box.y],
    180: [box.x + box.width, box.y + box.height],
    270: [box.x, box.y + box.height],
  }[rot] ?? [box.x, box.y];
  page.drawPage(layer, {
    x: origin[0],
    y: origin[1],
    xScale: width / layer.width,
    yScale: height / layer.height,
    rotate: degrees(rot),
  });
}

/** pdf-lib 读不了的文件先让 qpdf 重写一遍（修复损坏的交叉引用表） */
async function openForWriting(bytes: Uint8Array) {
  try {
    return await openPdfLib(bytes);
  } catch (first) {
    try {
      return await openPdfLib(await qpdfOne([], bytes));
    } catch {
      console.error('[ocr] pdf-lib 无法读取', first);
      throw new Error('这个 PDF 的结构有损坏，无法写入文字层。可以先用其他软件另存为 PDF 后再试');
    }
  }
}

// ---------------------------------------------------------------- 主流程

/** bytes 须未加密（先用 input.ts 的 unlockPdf 解开）。需要 qpdf、render、ocr 引擎。 */
export async function ocrPdf(bytes: Uint8Array, opts: OcrOptions): Promise<OcrResult> {
  const { report, signal } = opts;
  const check = () => {
    if (signal?.aborted) throw new Cancelled();
  };
  const doc = await openPdf(bytes);
  let worker: TessWorker | null = null;
  try {
    const total = doc.numPages;
    const targets = opts.range?.trim() ? expandRanges(opts.range, total) : Array.from({ length: total }, (_, i) => i);
    const texts = new Map<number, string>();
    const todo: number[] = [];
    if (opts.skipText) {
      report(null, '检查页面是否已有文字');
      for (const index of targets) {
        check();
        const text = await existingText(doc, index);
        if (text === null) todo.push(index);
        else texts.set(index, text);
      }
      if (!todo.length) {
        const which = targets.length === 1 ? '这一页' : opts.range?.trim() ? '所选页面都' : '每一页都';
        throw new Error(`${which}已有文字，不需要识别。如需重新识别，请关闭「跳过已有文字的页」`);
      }
    } else {
      todo.push(...targets);
    }

    const out = await openForWriting(bytes);
    check();

    // 识别进度来自 worker 的日志；加载阶段的英文状态换成中文
    let onRecognize: ((p: number) => void) | null = null;
    const logger = (m: LoggerMessage) => {
      if (m.status === 'recognizing text') onRecognize?.(m.progress);
      else if (STATUS[m.status]) report(null, STATUS[m.status]);
    };
    let currentDpi = 0;
    const failed: OcrResult['failed'] = [];
    const doubtful: number[] = [];

    for (const [k, index] of todo.entries()) {
      check();
      if (!worker) {
        report(null, '正在加载识别引擎');
        worker = await spawnWorker(opts.language, logger, signal);
        currentDpi = 0;
      }
      const tess = worker;
      const label = `识别第 ${k + 1}/${todo.length} 页`;
      report(k / todo.length, label);
      onRecognize = (p) => report((k + Math.min(1, Math.max(0, p))) / todo.length, label);
      try {
        const image = await renderGray(doc, index, opts.dpi);
        if (image.dpi !== currentDpi) {
          await tess.setParameters({ user_defined_dpi: String(image.dpi) });
          currentDpi = image.dpi;
        }
        const res = await abortable(
          tess.recognize(new Blob([image.pgm as BlobPart]), { pdfTitle: 'OCR', pdfTextOnly: true }, { text: true, pdf: true }),
          PAGE_TIMEOUT_MS,
          signal,
        );
        const raw = res.data.text ?? '';
        const text = raw.trim();
        texts.set(index, text);
        if (text && res.data.confidence < LOW_CONFIDENCE) doubtful.push(index + 1);
        // 类型声明写的是 number[]，实际是 Uint8Array
        const layer = await loadLayer(new Uint8Array(res.data.pdf as unknown as ArrayLike<number>), raw);
        placeLayer(out.getPage(index), await out.embedPage(layer));
      } catch (e) {
        if (e instanceof Cancelled) throw e;
        console.error(`[ocr] 第 ${index + 1} 页识别失败`, e);
        failed.push({ page: index + 1, reason: e instanceof Timeout ? '超时' : reason(e) });
        // 卡住或崩溃的 worker 不能再用，下一页换一个新的
        await tess.terminate().catch(() => {});
        worker = null;
      } finally {
        onRecognize = null;
      }
    }
    if (failed.length === todo.length) {
      throw new Error(`识别失败：${failed[0].reason}。可以改用 200 dpi 或缩小页码范围后重试`);
    }

    report(null, '正在生成 PDF');
    const pdf = await savePdfLib(out);
    const many = targets.length > 1;
    const text = targets
      .map((index) => {
        const body = texts.get(index) ?? (failed.some((f) => f.page === index + 1) ? '（这一页识别失败）' : '');
        return many ? `—— 第 ${index + 1} 页 ——\n${body}` : body;
      })
      .join('\n\n');
    return {
      pdf,
      text: text + '\n',
      empty: ![...texts.values()].some((t) => t.trim()),
      recognized: todo.length - failed.length,
      skipped: targets.length - todo.length,
      failed,
      doubtful,
    };
  } finally {
    await worker?.terminate().catch(() => {});
    await closePdf(doc);
  }
}
