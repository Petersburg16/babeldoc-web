// pdf-lib（@cantoo 维护的分支）：写文字/图片、改元数据、图片转 PDF。原版 pdf-lib 2021 年后不再维护，
// 也不能解密；@cantoo/fontkit 子集化思源/文楷这类长 loca 表的 TTF 时不会写坏字形（原版 fontkit 会）。
// 中文字体只在文字里真有非西文字符时才下载，西文直接用内置 Helvetica。
import type { PDFDocument, PDFFont, PDFPage } from '@cantoo/pdf-lib';
import { assetBytes, type EngineId, engines, type Progress } from '../engines.svelte';

export const loadPdfLib = () => import('@cantoo/pdf-lib');

export type PdfLib = Awaited<ReturnType<typeof loadPdfLib>>;

let fontkit: Promise<unknown> | null = null;
/** 子集嵌入 TTF 要用的 fontkit（只加载一次；模块默认导出要解一层） */
export function loadFontkit() {
  fontkit ??= import('@cantoo/fontkit').then((m) => (m as { default?: unknown }).default ?? m);
  return fontkit;
}

export type CjkFont = 'font-sans' | 'font-serif' | 'font-kai' | 'font-fang';

export const CJK_FONTS: { id: CjkFont; label: string; file: string }[] = [
  { id: 'font-sans', label: '思源黑体', file: 'SourceHanSansCN-Regular.ttf' },
  { id: 'font-serif', label: '思源宋体', file: 'SourceHanSerifCN-Regular.ttf' },
  { id: 'font-kai', label: '霞鹜文楷', file: 'LXGWWenKaiGB-Regular.1.520.ttf' },
  { id: 'font-fang', label: '朱雀仿宋', file: 'ZhuqueFangsong-Regular.ttf' },
];

/** WinAnsi（Helvetica 能画的字符）之外是否还有字符 */
export function needsCjkFont(text: string) {
  for (const ch of text) {
    const code = ch.codePointAt(0)!;
    if (code > 0xff && !'–—‘’“”•…€™'.includes(ch)) return true;
  }
  return false;
}

export interface TextFont {
  font: PDFFont;
  /** 字体里缺的字符（会显示为空白） */
  missing: string[];
}

/**
 * 给 text 选字体并嵌入 doc：纯西文用 Helvetica，否则下载（首次）并子集嵌入中文字体。
 * onProgress 报告字体下载进度。
 */
export async function fontForText(
  doc: PDFDocument,
  text: string,
  choice: CjkFont = 'font-sans',
  onProgress?: (p: Progress) => void,
): Promise<TextFont> {
  const { StandardFonts } = await loadPdfLib();
  if (!needsCjkFont(text)) return { font: await doc.embedFont(StandardFonts.Helvetica), missing: [] };
  const meta = CJK_FONTS.find((f) => f.id === choice) ?? CJK_FONTS[0];
  await engines.ensure([meta.id as EngineId], onProgress);
  const [kit, bytes] = await Promise.all([loadFontkit(), assetBytes(meta.id as EngineId, meta.file)]);
  doc.registerFontkit(kit as Parameters<PDFDocument['registerFontkit']>[0]);
  // liga 关掉：保留 fi 这类字母组合的可搜索性
  const font = await doc.embedFont(bytes, { subset: true, features: { liga: false } });
  const have = new Set(font.getCharacterSet());
  const missing = [...new Set([...text].filter((ch) => !/\s/u.test(ch) && !have.has(ch.codePointAt(0)!)))];
  return { font, missing };
}

/** 打开未加密的 PDF（加密文件先用 input.ts 的 unlockPdf 解开） */
export async function openPdfLib(bytes: Uint8Array) {
  const { PDFDocument } = await loadPdfLib();
  return PDFDocument.load(bytes, { updateMetadata: false });
}

/** 文档目录里的 XMP 元数据（按 UTF-8 解出的文本）；没有或解不开时为 undefined */
export function readXmp(lib: Pick<PdfLib, 'PDFName' | 'PDFRawStream' | 'decodePDFRawStream'>, doc: PDFDocument) {
  const { PDFName, PDFRawStream, decodePDFRawStream } = lib;
  try {
    const s = doc.catalog.lookup(PDFName.of('Metadata'));
    if (!(s instanceof PDFRawStream)) return undefined;
    return new TextDecoder('utf-8').decode(decodePDFRawStream(s).decode());
  } catch {
    return undefined;
  }
}

/** XMP 里声明的 PDF/A 级别（元素和属性两种写法都认），例如 { part: 2, level: 'B' }；不是 PDF/A 时为 null */
export function pdfaOf(xmp: string | undefined) {
  if (!xmp) return null;
  const part = /<pdfaid:part>\s*(\d)\s*</.exec(xmp) ?? /pdfaid:part\s*=\s*["'](\d)["']/.exec(xmp);
  if (!part) return null;
  const level = /<pdfaid:conformance>\s*([A-Za-z])\s*</.exec(xmp) ?? /pdfaid:conformance\s*=\s*["']([A-Za-z])["']/.exec(xmp);
  return { part: Number(part[1]), level: level?.[1].toUpperCase() ?? '' };
}

/**
 * 保存。默认用对象流压缩，体积更小；但 PDF/A-1 不允许对象流（pdf-lib 会直接报错），文件声明为 PDF/A-1 时自动不用。
 * objectStreams 显式传入时以它为准。不补空白页、不重新生成表单外观：只写出改过的内容（本站不经 pdf-lib 改表单）
 */
export async function savePdfLib(doc: PDFDocument, options: { objectStreams?: boolean } = {}) {
  const objectStreams = options.objectStreams ?? pdfaOf(readXmp(await loadPdfLib(), doc))?.part !== 1;
  return doc.save({ useObjectStreams: objectStreams, addDefaultPage: false, updateFieldAppearances: false });
}

/**
 * 让页面有自己的一份 Resources 再往里加字体和透明度。jsPDF、ReportLab 等生成的文件常让各页共用同一个
 * Resources（或其中的 Font 字典），也可能从页树继承；pdf-lib 会把这个共用字典直接挂到每页上，
 * 每页加的键大家都有，保存时又在每页内联写一遍，体积随页数平方增长（300 页 117 KB 的文件会变成上百 MB）。
 * 必须在该页第一次绘制之前调用。
 */
export function ownResources(lib: Pick<PdfLib, 'PDFArray' | 'PDFDict' | 'PDFName' | 'PDFRef'>, page: PDFPage) {
  const { PDFArray, PDFDict, PDFName, PDFRef } = lib;
  const node = page.node;
  const ctx = node.context;
  const res = node.Resources();
  const own = res ? res.clone(ctx) : ctx.obj({});
  for (const key of ['Font', 'XObject', 'ExtGState']) {
    const sub = own.lookupMaybe(PDFName.of(key), PDFDict);
    if (sub) own.set(PDFName.of(key), sub.clone(ctx));
  }
  node.set(PDFName.Resources, own);
  // Contents 指向几页共用的数组时，新加的内容流会出现在每一页上
  const contents = node.get(PDFName.Contents);
  if (contents instanceof PDFRef) {
    const array = ctx.lookup(contents);
    if (array instanceof PDFArray) node.set(PDFName.Contents, array.clone(ctx));
  }
}
