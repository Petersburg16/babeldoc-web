// 移植自 BentoPDF（AGPL-3.0）src/js/utils/pdf-operations.ts addPageNumbers，按本站引擎与界面重写：
// 支持“第 1 页”这类中文格式（只在格式含中文时才下载中文字体），按每页的 /Rotate 与 CropBox 摆正位置，可跳过封面。
import {
  beginMarkedContent,
  degrees,
  endMarkedContent,
  PDFArray,
  PDFDict,
  PDFName,
  PDFRef,
  rgb,
  type PDFDocument,
  type PDFFont,
  type PDFPage,
} from '@cantoo/pdf-lib';
import { errorText } from '../../format';
import { assetBytes } from '../engines.svelte';
import type { Report } from '../files';
import { type CjkFont, fontForText, needsCjkFont, openPdfLib, ownResources } from '../engines/pdflib';
import { expandRanges } from '../ranges';
import { embedStandard, saveDoc } from './metadata';

export type NumberFormat = 'n' | 'n-of-total' | 'dash' | 'zh' | 'zh-of-total';
export type Vertical = 'top' | 'bottom';
export type Align = 'left' | 'center' | 'right';

export const NUMBER_FORMATS: { id: NumberFormat; template: string }[] = [
  { id: 'n', template: '{n}' },
  { id: 'n-of-total', template: '{n} / {total}' },
  { id: 'dash', template: '- {n} -' },
  { id: 'zh', template: '第 {n} 页' },
  { id: 'zh-of-total', template: '第 {n} 页 / 共 {total} 页' },
];

export function formatLabel(format: NumberFormat, n: number | string, total: number | string) {
  const template = NUMBER_FORMATS.find((f) => f.id === format)?.template ?? '{n}';
  return template.replaceAll('{n}', String(n)).replaceAll('{total}', String(total));
}

export interface PageNumberOptions {
  format: NumberFormat;
  vertical: Vertical;
  align: Align;
  /** 第一个编号页上印的数字 */
  start: number;
  /** 要加页码的页（本站页码写法），空表示全部 */
  pages: string;
  fontSize: number;
  /** 到可见页边的距离（pt） */
  margin: number;
  font: CjkFont;
}

// 页面在阅读器里的样子：CropBox 按 /Rotate 转正后的坐标系（左下为原点）。toUser 把它换回页面自身坐标。
interface VisualFrame {
  w: number;
  h: number;
  rot: number;
  toUser(vx: number, vy: number): { x: number; y: number };
}

function visualFrame(page: PDFPage): VisualFrame {
  const { x: x0, y: y0, width: W, height: H } = page.getCropBox();
  const r = (((Math.round(page.getRotation().angle / 90) * 90) % 360) + 360) % 360;
  switch (r) {
    case 90:
      return { w: H, h: W, rot: 90, toUser: (vx, vy) => ({ x: x0 + W - vy, y: y0 + vx }) };
    case 180:
      return { w: W, h: H, rot: 180, toUser: (vx, vy) => ({ x: x0 + W - vx, y: y0 + H - vy }) };
    case 270:
      return { w: H, h: W, rot: 270, toUser: (vx, vy) => ({ x: x0 + vy, y: y0 + H - vx }) };
    default:
      return { w: W, h: H, rot: 0, toUser: (vx, vy) => ({ x: x0 + vx, y: y0 + vy }) };
  }
}

// 内置的 Helvetica 不嵌入文件，PDF/A 等标准不允许。改用字宽与它相同的 Liberation Sans（pdf.js 自带，约 140 KB）子集嵌入
async function embeddedLatinFont(doc: PDFDocument): Promise<PDFFont> {
  const [kit, bytes] = await Promise.all([
    import('@cantoo/fontkit').then((m) => (m as { default?: unknown }).default ?? m),
    assetBytes('render', 'standard_fonts/LiberationSans-Regular.ttf'),
  ]);
  doc.registerFontkit(kit as Parameters<PDFDocument['registerFontkit']>[0]);
  return doc.embedFont(bytes, { subset: true });
}

/** bytes 需未加密（先用 unlockPdf 解开）。返回的 standard 是原文件声明的 PDF/A 等标准，这时页码字体一定嵌入 */
export async function addPageNumbers(bytes: Uint8Array, o: PageNumberOptions, report?: Report) {
  const doc = await openPdfLib(bytes);
  const pages = doc.getPages();
  let targets: number[];
  try {
    // 按文档顺序编号，“5-,1”也是先第 1 页
    targets = (o.pages.trim() ? expandRanges(o.pages, pages.length) : pages.map((_, i) => i)).sort((a, b) => a - b);
  } catch (e) {
    throw new Error(`要加页码的页有误：${errorText(e)}（这个文件共 ${pages.length} 页）`);
  }
  // N 取最后一个印出来的数字：跳过封面并从 2 起编时，末页是“第 10 页 / 共 10 页”而不是“共 9 页”
  const last = o.start + targets.length - 1;
  const labels = targets.map((_, k) => formatLabel(o.format, o.start + k, last));

  const standard = embedStandard(doc);
  let font: PDFFont;
  if (standard && !needsCjkFont(labels.join(''))) {
    report?.(null, `${standard} 文件需嵌入字体，正在载入`);
    font = await embeddedLatinFont(doc);
  } else {
    ({ font } = await fontForText(doc, labels.join(''), o.font, (p) => {
      if (p.loaded < p.total) report?.(p.loaded / p.total, `正在下载${p.label}`);
    }));
  }
  report?.(null, '正在添加页码并保存');

  const size = o.fontSize;
  const ascent = font.heightAtSize(size, { descender: false });
  const descent = font.heightAtSize(size) - ascent;
  const color = rgb(0, 0, 0);

  targets.forEach((index, k) => {
    const page = pages[index];
    const f = visualFrame(page);
    const label = labels[k];
    const width = font.widthOfTextAtSize(label, size);
    // 很小的页面上边距不超过页宽高的四分之一，免得页码跑出页面
    const m = Math.min(o.margin, f.w / 4, f.h / 4);
    const vx = o.align === 'left' ? m : o.align === 'right' ? f.w - m - width : (f.w - width) / 2;
    const vy = o.vertical === 'bottom' ? m + descent : f.h - m - ascent;
    const { x, y } = f.toUser(vx, vy);
    // 先给这页一份自己的资源字典，免得各页共用资源的文件体积随页数平方增长
    ownResources({ PDFArray, PDFDict, PDFName, PDFRef }, page);
    // 标成版面附属内容：带标签的文件（PDF/UA 等）里屏幕阅读器会跳过页码，也不算没有标签的正文
    page.pushOperators(beginMarkedContent('Artifact'));
    page.drawText(label, { x, y, size, font, color, rotate: degrees(f.rot) });
    page.pushOperators(endMarkedContent());
  });

  return { bytes: await saveDoc(doc), count: targets.length, standard };
}
