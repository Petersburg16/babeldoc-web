// pdf-lib（@cantoo 维护的分支）：写文字/图片、改元数据、图片转 PDF。原版 pdf-lib 2021 年后不再维护，
// 也不能解密；@cantoo/fontkit 子集化思源/文楷这类长 loca 表的 TTF 时不会写坏字形（原版 fontkit 会）。
// 中文字体只在文字里真有非西文字符时才下载，西文直接用内置 Helvetica。
import type { PDFDocument, PDFFont } from '@cantoo/pdf-lib';
import { assetBytes, type EngineId, engines, type Progress } from '../engines.svelte';

export const loadPdfLib = () => import('@cantoo/pdf-lib');

let fontkit: Promise<unknown> | null = null;
function loadFontkit() {
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

/** 保存；保留对象流压缩，体积更小 */
export async function savePdfLib(doc: PDFDocument) {
  return doc.save({ useObjectStreams: true });
}
