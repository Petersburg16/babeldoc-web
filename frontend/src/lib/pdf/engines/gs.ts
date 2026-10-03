// Ghostscript（WASM）：压缩和转 PDF/A 都靠它把整个文件重写一遍（pdfwrite 设备）。
// 移植自 BentoPDF（AGPL-3.0）src/js/utils/ghostscript-loader.ts，按本站引擎与界面重写：
// 放进 worker 运行（callMain 是同步的，BentoPDF 在主线程跑会卡住页面），参数按实测修正，
// 前后各加一道 pdf-lib 处理（保住链接、修正中文字体名和文字提取）。一个任务一个 worker，用完即关，释放 WASM 内存。
import type { PDFDocument, PDFObject } from '@cantoo/pdf-lib';
import { assetUrl } from '../engines.svelte';
import type { Report } from '../files';
import { Cancelled, unlockPdf } from '../input';
import type { GsMessage, GsRequest } from './gs.worker';
import { loadPdfLib, openPdfLib } from './pdflib';

export class GsError extends Error {}

export interface GsResult {
  code: number;
  files: Record<string, Uint8Array>;
  log: string[];
}

/** 不要加 -dQUIET：进度要靠 “Processing pages … / Page N” 这些输出 */
export const GS_BASE = ['-dSAFER', '-dBATCH', '-dNOPAUSE', '-sDEVICE=pdfwrite', '-dAutoRotatePages=/None'];

/** 图片原样保留：JPEG/JPX 直接透传，其余无损 Flate，不降采样 */
export const LOSSLESS_IMAGES = [
  '-dPassThroughJPEGImages=true',
  '-dPassThroughJPXImages=true',
  '-dDownsampleColorImages=false',
  '-dDownsampleGrayImages=false',
  '-dDownsampleMonoImages=false',
  '-dAutoFilterColorImages=false',
  '-dAutoFilterGrayImages=false',
  '-sColorImageFilter=FlateEncode',
  '-sGrayImageFilter=FlateEncode',
];

const CRASHED = 'Ghostscript 引擎意外退出，多半是内存不足：请关闭其他标签页后重试，或换用电脑上的浏览器';

const absolute = (url: string) => new URL(url, location.origin).href;

/** 跑一条 Ghostscript 命令。inputs 写入 worker 的内存文件系统（复制后转交，调用方的数据不受影响） */
export function runGhostscript(
  args: string[],
  inputs: Record<string, Uint8Array | string>,
  outputs: string[],
  onProgress?: (page: number, total: number) => void,
): Promise<GsResult> {
  const worker = new Worker(new URL('./gs.worker.ts', import.meta.url), { type: 'module', name: 'ghostscript' });
  const copies: Record<string, Uint8Array | string> = {};
  const transfer: ArrayBuffer[] = [];
  for (const [path, data] of Object.entries(inputs)) {
    const copy = typeof data === 'string' ? data : data.slice();
    copies[path] = copy;
    if (typeof copy !== 'string') transfer.push(copy.buffer as ArrayBuffer);
  }
  return new Promise<GsResult>((resolve, reject) => {
    worker.onmessage = (e: MessageEvent<GsMessage>) => {
      const m = e.data;
      if (m.type === 'progress') onProgress?.(m.page, m.total);
      else if (m.type === 'done') resolve(m);
      else {
        console.error('Ghostscript 加载失败', m.message, m.log);
        reject(new GsError(/memory|allocate/i.test(m.message) ? CRASHED : `Ghostscript 引擎加载失败：${m.message}`));
      }
    };
    worker.onerror = (e) => {
      e.preventDefault();
      reject(new GsError(CRASHED));
    };
    const request: GsRequest = {
      jsUrl: absolute(assetUrl('ghostscript', 'gs.js')),
      wasmUrl: absolute(assetUrl('ghostscript', 'gs.wasm')),
      args,
      inputs: copies,
      outputs,
    };
    worker.postMessage(request, transfer);
  }).finally(() => worker.terminate());
}

/** 最后一条 Ghostscript 报错，去掉星号前缀 */
function lastError(log: string[]) {
  const line = [...log].reverse().find((l) => /\*\*\*\*|Error/.test(l) && !/Output may be incorrect/.test(l));
  return line?.replace(/\*+/g, '').trim() ?? '';
}

/**
 * PDF → PDF：input 写到 /tmp/in.pdf，before 里的 PostScript 文件（如 PDF/A 定义）先于输入执行。
 * Ghostscript 缺密码时也返回 0 并写出一个空壳文件，所以要看日志判断成败。
 */
export async function gsPdf(
  args: string[],
  input: Uint8Array,
  options: { before?: Record<string, string>; onProgress?: (page: number, total: number) => void } = {},
) {
  const before = options.before ?? {};
  const r = await runGhostscript(
    [...args, '-sOutputFile=/tmp/out.pdf', ...Object.keys(before), '/tmp/in.pdf'],
    { ...before, '/tmp/in.pdf': input },
    ['/tmp/out.pdf'],
    options.onProgress,
  );
  const text = r.log.join('\n');
  if (/requires a password/i.test(text)) throw new GsError('这个 PDF 需要打开密码');
  if (r.code !== 0 || /No pages will be processed/.test(text)) {
    console.error('Ghostscript 失败', r.code, r.log);
    const detail = lastError(r.log);
    throw new GsError(`Ghostscript 无法处理这个文件${detail ? `（${detail}）` : ''}，文件可能已损坏：可以先用浏览器打开、另存为 PDF 后再试`);
  }
  const output = r.files['/tmp/out.pdf'];
  if (!output || output.length < 64) throw new GsError('Ghostscript 没有生成文件，文件可能已损坏');
  return { output, log: r.log };
}

/** 交给 Ghostscript 前先解密（只限制权限的也解开，需要密码时询问）；读不了的文件给出中文提示。需要 qpdf 引擎 */
export async function unlockForGs(file: File, bytes: Uint8Array) {
  try {
    return await unlockPdf(file, bytes);
  } catch (e) {
    if (e instanceof Cancelled) throw e;
    console.error(e);
    throw new Error('无法读取这个文件：可能已损坏、不是 PDF，或使用了不支持的加密方式');
  }
}

/** 日志里出现 “Loading CIDFont … substitute”：有未嵌入的中日韩字体被替代 */
export const substitutedCid = (log: string[]) => log.some((l) => l.startsWith('Loading CIDFont'));

export interface FileFailure {
  name: string;
  message: string;
}

/** 逐个处理多个文件：某个出错时记下原因、接着处理其余的，全部出错才报错；取消则立即停止 */
export async function eachFile<T extends object>(
  files: File[],
  verb: string,
  report: Report,
  job: (file: File, onProgress: (fraction: number) => void) => Promise<T>,
) {
  const done: (T & { name: string })[] = [];
  const failed: FileFailure[] = [];
  for (const [i, file] of files.entries()) {
    const label = files.length > 1 ? `${verb}「${file.name}」（${i + 1}/${files.length}）` : `${verb}「${file.name}」`;
    report(i / files.length, label);
    try {
      done.push({ ...(await job(file, (f) => report((i + f) / files.length, label))), name: file.name });
    } catch (e) {
      if (e instanceof Cancelled || files.length === 1) throw e;
      console.error(e);
      failed.push({ name: file.name, message: e instanceof Error ? e.message : String(e) });
    }
  }
  if (!done.length) {
    const shown = failed.slice(0, 3).map((f) => `「${f.name}」${f.message}`);
    if (failed.length > 3) shown.push(`另有 ${failed.length - 3} 个文件也没能处理`);
    throw new Error(shown.join('；'));
  }
  return { done, failed };
}

// ---- pdf-lib 前后处理 ----

/** pdf-lib 读不了少数出版社的 PDF：前后处理出错只跳过这一步，不影响 Ghostscript 的结果 */
export async function attempt<T>(job: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await job();
  } catch (e) {
    console.warn('pdf-lib 前后处理失败，已跳过', e);
    return fallback;
  }
}

export const tryOpen = (bytes: Uint8Array) => attempt<PDFDocument | null>(() => openPdfLib(bytes), null);

export async function saveDoc(doc: PDFDocument, objectStreams: boolean) {
  return doc.save({ useObjectStreams: objectStreams, addDefaultPage: false, updateFieldAppearances: false });
}

/** 按 Unicode 编码的 CMap：字符码就是 UTF-16 码位 */
const UNICODE_CMAP = /^Uni(GB|CNS|JIS|KS)-(UTF16|UCS2)-[HV]$/;

/** Adobe 的中日韩字符集：字符编号是公共的，Ghostscript 的替代字体能对上 */
const ADOBE_CJK = new Set(['GB1', 'CNS1', 'Japan1', 'Japan2', 'Korea1', 'KR']);

export interface Structure {
  outlines: boolean;
  tagged: boolean;
  fields: number;
  links: number;
  /** 未嵌入、按 Unicode 编码的 CID 字体（替代后能修好文字提取） */
  cjkUnicode: number;
  /** 其他未嵌入、用 Adobe 字符集的 CID 字体（替代后字形或文字提取可能出错） */
  cjkOther: number;
  /**
   * 所有未嵌入的 CID 字体。identity：字符集不是 Adobe 的（多为 Identity），字符编号就是原字体自己的字形号，
   * 换成任何替代字体都会画成别的字（WPS、iText 不嵌入字体时常见）
   */
  missing: { name: string; identity: boolean }[];
}

/** 名称按字节存储：先按 UTF-8 解，不是再按 GBK（知网等中文字体名） */
function decodeName(text: string) {
  const raw = Uint8Array.from(text, (c) => c.charCodeAt(0) & 0xff);
  if (raw.every((b) => b < 0x80)) return text;
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(raw);
  } catch {
    return new TextDecoder('gbk').decode(raw);
  }
}

/** 提示里列出的字体名：去掉子集前缀，最多三个 */
export function fontList(names: string[]) {
  const unique = [...new Set(names.map((n) => decodeName(n).replace(/^[A-Z]{6}\+/, '')).filter(Boolean))];
  return unique.length > 3 ? `${unique.slice(0, 3).join('、')} 等` : unique.join('、');
}

/**
 * Ghostscript 替代了哪些“字符编号是原字体字形号”的字体：这些字会全部画错，复制出来也是错的。
 * 以日志为准（只统计页面真正用到的字体）；日志里的名字对不上（如 GBK 字体名）时宁可全算上。
 */
export function wrongGlyphFonts(before: Structure | null, log: string[]) {
  const identity = before?.missing.filter((f) => f.identity).map((f) => f.name) ?? [];
  const substituted = log.flatMap((l) => /^Loading CIDFont (.+?) substitute from/.exec(l)?.[1] ?? []);
  if (!identity.length || !substituted.length) return [];
  const hit = identity.filter((n) => substituted.includes(n));
  if (hit.length) return hit;
  const others = new Set(before?.missing.filter((f) => !f.identity).map((f) => f.name));
  return substituted.some((n) => !others.has(n)) ? identity : [];
}

async function fontsOf(doc: PDFDocument) {
  const { PDFDict, PDFName, PDFStream } = await loadPdfLib();
  const type0 = [];
  for (const [, obj] of doc.context.enumerateIndirectObjects()) {
    if (obj instanceof PDFDict && obj.get(PDFName.of('Subtype')) === PDFName.of('Type0')) type0.push(obj);
  }
  // 编码可以是预定义 CMap 的名字，也可以是内嵌的 CMap 流（Ghostscript 输出 PDF/A 时会内嵌）
  const cmapName = (enc: PDFObject | undefined) => {
    if (enc instanceof PDFName) return enc.decodeText();
    const name = enc instanceof PDFStream ? enc.dict.get(PDFName.of('CMapName')) : undefined;
    return name instanceof PDFName ? name.decodeText() : '';
  };
  return type0.map((font) => ({ font, unicode: UNICODE_CMAP.test(cmapName(font.lookup(PDFName.of('Encoding')))) }));
}

export async function inspect(doc: PDFDocument): Promise<Structure> {
  const { PDFArray, PDFDict, PDFHexString, PDFName, PDFString } = await loadPdfLib();
  const N = (s: string) => PDFName.of(s);
  const catalog = doc.catalog;
  const outlines = catalog.lookup(N('Outlines'));
  const acro = catalog.lookup(N('AcroForm'));
  const fields = acro instanceof PDFDict ? acro.lookup(N('Fields')) : undefined;
  let links = 0;
  for (const page of doc.getPages()) {
    const annots = page.node.Annots();
    for (let i = 0; annots && i < annots.size(); i++) {
      const a = annots.lookup(i);
      if (a instanceof PDFDict && a.get(N('Subtype')) === N('Link')) links++;
    }
  }
  let cjkUnicode = 0;
  let cjkOther = 0;
  const missing: Structure['missing'] = [];
  for (const { font, unicode } of await fontsOf(doc)) {
    const descendants = font.lookup(N('DescendantFonts'));
    const cid = descendants instanceof PDFArray ? descendants.lookup(0) : undefined;
    const fd = cid instanceof PDFDict ? cid.lookup(N('FontDescriptor')) : undefined;
    if (fd instanceof PDFDict && ['FontFile', 'FontFile2', 'FontFile3'].some((k) => fd.has(N(k)))) continue;
    const info = cid instanceof PDFDict ? cid.lookup(N('CIDSystemInfo')) : undefined;
    const ordering = info instanceof PDFDict ? info.lookup(N('Ordering')) : undefined;
    const collection = ordering instanceof PDFString || ordering instanceof PDFHexString ? ordering.decodeText() : '';
    // Ghostscript 日志里报的是子字体的名字
    const baseFont = (cid instanceof PDFDict ? cid.get(N('BaseFont')) : undefined) ?? font.get(N('BaseFont'));
    const identity = cid instanceof PDFDict && !ADOBE_CJK.has(collection);
    missing.push({ name: baseFont instanceof PDFName ? baseFont.decodeText() : '', identity });
    if (identity) continue;
    if (unicode) cjkUnicode++;
    else cjkOther++;
  }
  return {
    outlines: outlines instanceof PDFDict && outlines.has(N('First')),
    tagged: catalog.has(N('StructTreeRoot')),
    fields: fields instanceof PDFArray ? fields.size() : 0,
    links,
    cjkUnicode,
    cjkOther,
    missing,
  };
}

/** Ghostscript 重写前后的结构对比，给出提示 */
export function structureWarnings(before: Structure | null, after: Structure | null, substituted: boolean, encrypted: boolean) {
  const warnings: string[] = [];
  if (encrypted) warnings.push('原文件的密码和权限限制已去除');
  if (!before) warnings.push('这个文件的结构无法预先检查，书签、链接是否保留请自行核对');
  if (before?.outlines && after && !after.outlines) warnings.push('书签没能保留');
  if (before && after && after.links < before.links) {
    warnings.push(`部分链接没能保留（剩 ${after.links} / ${before.links} 个）`);
  }
  if (before?.tagged) warnings.push('无障碍结构标签已去除');
  if (before?.fields) warnings.push('表单已变成普通页面内容，不能再填写');
  if (substituted) {
    warnings.push(
      !before
        ? '原文件有未嵌入的字体，已用替代字体嵌入：可能有字显示不对，复制、搜索也可能出错，请核对结果'
        : before.cjkOther
          ? '原文件有未嵌入的字体，已用替代字体嵌入：个别字可能显示不对，复制、搜索也可能出错，请核对结果'
          : '原文件有未嵌入的中文字体，已用内置字体替代并嵌入，字形与原来略有不同',
    );
  }
  return warnings;
}

/**
 * PDF/A 要求批注带“打印”标志，没有的会被 Ghostscript 整个去掉（LaTeX 的链接全是这样）。
 * 给可见的批注补上打印标志；本来就隐藏的保持原样。返回改动的数量。
 */
export async function markPrintable(doc: PDFDocument) {
  const { PDFDict, PDFName, PDFNumber } = await loadPdfLib();
  const N = (s: string) => PDFName.of(s);
  let changed = 0;
  for (const page of doc.getPages()) {
    const annots = page.node.Annots();
    for (let i = 0; annots && i < annots.size(); i++) {
      const a = annots.lookup(i);
      if (!(a instanceof PDFDict) || a.get(N('Subtype')) === N('Popup')) continue;
      const f = a.lookup(N('F'));
      const flags = f instanceof PDFNumber ? f.asNumber() : 0;
      if (flags & (1 | 2 | 32)) continue; // Invisible / Hidden / NoView
      const next = (flags | 4) & ~256; // 加 Print，去 ToggleNoView
      if (next !== flags) {
        a.set(N('F'), PDFNumber.of(next));
        changed++;
      }
    }
  }
  return changed;
}

/** 名称树（Names / Kids 嵌套）里的条目数 */
async function countNames(node: PDFObject | undefined, depth = 0): Promise<number> {
  const { PDFArray, PDFDict, PDFName } = await loadPdfLib();
  if (!(node instanceof PDFDict) || depth > 32) return 0;
  const names = node.lookup(PDFName.of('Names'));
  let n = names instanceof PDFArray ? Math.floor(names.size() / 2) : 0;
  const kids = node.lookup(PDFName.of('Kids'));
  for (let i = 0; kids instanceof PDFArray && i < kids.size(); i++) n += await countNames(kids.lookup(i), depth + 1);
  return n;
}

/**
 * PDF/A 的附件要另带关联说明（2b 还要求附件本身是 PDF/A），Ghostscript 不补这些、却原样保留附件，验证不过。
 * 去掉文档附件和附件批注（连同它的弹出窗口），返回去掉的附件数。
 */
export async function stripAttachments(doc: PDFDocument) {
  const { PDFDict, PDFName, PDFRef } = await loadPdfLib();
  const N = (s: string) => PDFName.of(s);
  const catalog = doc.catalog;
  let removed = 0;
  const names = catalog.lookup(N('Names'));
  if (names instanceof PDFDict && names.has(N('EmbeddedFiles'))) {
    removed += await countNames(names.lookup(N('EmbeddedFiles')));
    names.delete(N('EmbeddedFiles'));
    if (!names.keys().length) catalog.delete(N('Names'));
  }
  catalog.delete(N('AF'));
  if (catalog.get(N('PageMode')) === N('UseAttachments')) catalog.delete(N('PageMode'));
  for (const page of doc.getPages()) {
    const annots = page.node.Annots();
    if (!annots) continue;
    const drop = new Set<PDFObject>();
    for (let i = 0; i < annots.size(); i++) {
      const a = annots.lookup(i);
      if (!(a instanceof PDFDict) || a.get(N('Subtype')) !== N('FileAttachment')) continue;
      drop.add(annots.get(i));
      const popup = a.get(N('Popup'));
      if (popup instanceof PDFRef) drop.add(popup);
      removed++;
    }
    for (let i = annots.size() - 1; i >= 0; i--) if (drop.has(annots.get(i))) annots.remove(i);
  }
  return removed;
}

/** 文件里是否还有嵌入的文件（附件、附件批注等） */
export async function hasEmbeddedFiles(doc: PDFDocument) {
  const { PDFArray, PDFDict, PDFName, PDFStream } = await loadPdfLib();
  const EF = PDFName.of('EF');
  const visit = (obj: PDFObject, depth: number): boolean => {
    if (obj instanceof PDFStream) return obj.dict.get(PDFName.of('Type')) === PDFName.of('EmbeddedFile');
    if (depth > 8) return false;
    if (obj instanceof PDFDict) return obj.has(EF) || obj.values().some((v) => visit(v, depth + 1));
    if (obj instanceof PDFArray) return obj.asArray().some((v) => visit(v, depth + 1));
    return false;
  };
  for (const [, obj] of doc.context.enumerateIndirectObjects()) if (visit(obj, 0)) return true;
  return false;
}

let identityCMap: string | null = null;

/** 码位即 Unicode 的 ToUnicode CMap（双字节，跳过代理区） */
function identityUcs() {
  if (identityCMap) return identityCMap;
  const ranges = [];
  for (let hi = 0; hi < 256; hi++) {
    if (hi >= 0xd8 && hi <= 0xdf) continue;
    const h = hi.toString(16).padStart(2, '0');
    ranges.push(`<${h}00><${h}ff><${h}00>`);
  }
  const blocks = [];
  for (let i = 0; i < ranges.length; i += 100) {
    const chunk = ranges.slice(i, i + 100);
    blocks.push(`${chunk.length} beginbfrange\n${chunk.join('\n')}\nendbfrange`);
  }
  identityCMap = [
    '/CIDInit /ProcSet findresource begin',
    '12 dict begin',
    'begincmap',
    '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def',
    '/CMapName /Adobe-Identity-UCS def',
    '/CMapType 2 def',
    '1 begincodespacerange',
    '<0000><ffff>',
    'endcodespacerange',
    ...blocks,
    'endcmap',
    'CMapName currentdict /CMap defineresource pop',
    'end',
    'end',
    '',
  ].join('\n');
  return identityCMap;
}

/**
 * Ghostscript 用内置字体替代未嵌入的中文字体（UniGB-UTF16-H 这类编码）时，写出的 ToUnicode 是错的，
 * 复制出来是乱码。这类编码的字符码本身就是 Unicode，换成恒等映射即可。返回修正的字体数。
 */
export async function fixUnicodeCMaps(doc: PDFDocument) {
  const { PDFName } = await loadPdfLib();
  const fonts = (await fontsOf(doc)).filter((f) => f.unicode);
  if (!fonts.length) return 0;
  const ref = doc.context.register(doc.context.flateStream(identityUcs()));
  for (const { font } of fonts) font.set(PDFName.of('ToUnicode'), ref);
  return fonts.length;
}

/**
 * PDF/A-2/3 要求名称是 UTF-8（规则 6.1.8）；知网等 PDF 的字体名是 GBK 编码（如 华光黑体_CNKI），
 * 转成 UTF-8。返回修正的名称数。
 */
export async function fixGbkFontNames(doc: PDFDocument) {
  const { PDFDict, PDFName, PDFStream } = await loadPdfLib();
  const utf8 = new TextDecoder('utf-8', { fatal: true });
  const gbk = new TextDecoder('gbk');
  const encoder = new TextEncoder();
  let changed = 0;
  for (const [, obj] of doc.context.enumerateIndirectObjects()) {
    const dict = obj instanceof PDFDict ? obj : obj instanceof PDFStream ? obj.dict : null;
    const type = dict?.get(PDFName.of('Type'));
    if (!dict || (type !== PDFName.of('Font') && type !== PDFName.of('FontDescriptor'))) continue;
    for (const key of ['BaseFont', 'FontName']) {
      const value = dict.get(PDFName.of(key));
      if (!(value instanceof PDFName)) continue;
      const raw = Uint8Array.from(value.decodeText(), (c) => c.charCodeAt(0) & 0xff);
      if (raw.every((b) => b < 0x80)) continue;
      try {
        utf8.decode(raw);
        continue;
      } catch {
        /* 不是 UTF-8，按 GBK 转 */
      }
      const fixed = encoder.encode(gbk.decode(raw));
      if (fixed.length > 127) continue; // 名称最长 127 字节
      dict.set(PDFName.of(key), PDFName.of(String.fromCharCode(...fixed)));
      changed++;
    }
  }
  return changed;
}
