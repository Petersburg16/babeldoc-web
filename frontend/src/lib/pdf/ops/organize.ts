// 移植自 BentoPDF（AGPL-3.0）src/js/logic/organize-pdf-page.ts、pdf-multi-tool.ts，按本站引擎与界面重写：
// 整理结果用一次 qpdf --pages 生成（以原文件为主文件，书签、元数据、链接都保留，页面内容不重写），
// 空白页来自一个现写的极简 PDF，用户的旋转用 --rotate=+N 叠加在页面原有的 /Rotate 上。
// 缩略图由 pdf.js 按需渲染成 JPEG blob，画布用完立即释放，几百页的文件也只占几 MB。
import { canvasToBlob, closePdf, openPdf, releaseCanvas, renderPage } from '../engines/pdfjs';
import { QpdfError, runQpdf } from '../engines/qpdf';

/** 整理后的一页：原文件的第 index 页（0 起计），或一张空白页。rotate 为用户叠加的角度，可累计（便于动画），导出时归一化 */
export type Slot =
  | { id: string; kind: 'page'; index: number; rotate: number }
  | { id: string; kind: 'blank'; width: number; height: number; rotate: number };

/** 页面显示尺寸（pt），已计入页面自身的 /Rotate */
export interface PageInfo {
  width: number;
  height: number;
}

/** 归一化到 0/90/180/270 */
export function normalizeRotation(deg: number) {
  return (((deg % 360) + 360) % 360) as 0 | 90 | 180 | 270;
}

/** 每个尺寸一页空白页的极简 PDF（qpdf --check 无错误），不需要 pdf-lib */
export function blankPdf(sizes: [number, number][]): Uint8Array {
  const objs: string[] = ['<< /Type /Catalog /Pages 2 0 R >>', ''];
  const kids: string[] = [];
  for (const [w, h] of sizes) {
    kids.push(`${objs.length + 1} 0 R`);
    objs.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${w.toFixed(2)} ${h.toFixed(2)}] /Resources << >> >>`);
  }
  objs[1] = `<< /Type /Pages /Kids [${kids.join(' ')}] /Count ${sizes.length} >>`;
  let s = '%PDF-1.4\n';
  const offsets: number[] = [];
  // 全是 ASCII，字符串长度即字节数
  objs.forEach((o, i) => {
    offsets.push(s.length);
    s += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = s.length;
  s += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n`;
  s += offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('');
  s += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return new TextEncoder().encode(s);
}

/** 1 起计的页号列表压成 qpdf 写法：连续递增的合并为 a-b */
function compact(pages: number[]) {
  const parts: string[] = [];
  for (let i = 0; i < pages.length; i++) {
    const start = pages[i];
    while (i + 1 < pages.length && pages[i + 1] === pages[i] + 1) i++;
    parts.push(start === pages[i] ? `${start}` : `${start}-${pages[i]}`);
  }
  return parts.join(',');
}

/** qpdf 参数：原文件为主文件（"."），连续来自同一文件的页并成一段；旋转按输出页号、相对原角度叠加 */
export function organizeArgs(slots: Slot[]) {
  const args = ['/src.pdf', '--remove-unreferenced-resources=yes', '--object-streams=generate', '--pages'];
  let blankNo = 0;
  let runFile = '';
  let runPages: number[] = [];
  for (const s of slots) {
    const file = s.kind === 'page' ? '.' : '/blank.pdf';
    const page = s.kind === 'page' ? s.index + 1 : ++blankNo;
    if (file !== runFile && runPages.length) {
      args.push(runFile, compact(runPages));
      runPages = [];
    }
    runFile = file;
    runPages.push(page);
  }
  if (runPages.length) args.push(runFile, compact(runPages));
  args.push('--');
  const byAngle = new Map<number, number[]>();
  slots.forEach((s, i) => {
    const angle = normalizeRotation(s.rotate);
    if (angle) byAngle.set(angle, [...(byAngle.get(angle) ?? []), i + 1]);
  });
  for (const [angle, pages] of byAngle) args.push(`--rotate=+${angle}:${compact(pages)}`);
  args.push('/out.pdf');
  return args;
}

/** 按 slots 生成新 PDF；bytes 须已解密（input.ts 的 unlockPdf） */
export async function organizePdf(bytes: Uint8Array, slots: Slot[]) {
  if (!slots.some((s) => s.kind === 'page')) throw new Error('至少保留原文件的一页');
  const blanks = slots.filter((s): s is Extract<Slot, { kind: 'blank' }> => s.kind === 'blank');
  const inputs: Record<string, Uint8Array> = { 'src.pdf': bytes };
  if (blanks.length) inputs['blank.pdf'] = blankPdf(blanks.map((b) => [b.width, b.height]));
  const r = await runQpdf(organizeArgs(slots), inputs);
  const out = r.files['out.pdf'];
  if (!out) throw new QpdfError(r.code, r.log || '没有生成文件');
  return out;
}

// 缩略图放在 4:5 的格子里，按设备像素比渲染（最多 2 倍），保证高分屏也清晰
const THUMB_W = 180;
const THUMB_H = 225;

export interface PageSource {
  pages: PageInfo[];
  /** 某页的缩略图卡片进入或离开可视区（同一原页可能有多张卡片，按次数计） */
  want(index: number, visible: boolean): void;
  dispose(): Promise<void>;
}

/**
 * 打开 PDF 读出各页尺寸，返回懒加载缩略图的队列：只渲染可视区附近的页，并发 2。
 * onThumb 收到 blob URL（渲染失败为 null）；dispose 时统一回收。
 */
export async function openPages(bytes: Uint8Array, onThumb: (index: number, url: string | null) => void): Promise<PageSource> {
  const doc = await openPdf(bytes);
  let pages: PageInfo[];
  try {
    pages = await Promise.all(
      Array.from({ length: doc.numPages }, async (_, i) => {
        const vp = (await doc.getPage(i + 1)).getViewport({ scale: 1 });
        return { width: vp.width, height: vp.height };
      }),
    );
  } catch (e) {
    await closePdf(doc);
    throw e;
  }

  const dpr = Math.min(globalThis.devicePixelRatio || 1, 2);
  const visible = new Map<number, number>();
  const settled = new Set<number>();
  const urls: string[] = [];
  let waiting: number[] = [];
  let active = 0;
  let closed = false;

  async function render(index: number) {
    try {
      const { width, height } = pages[index];
      const target = Math.round(Math.min(THUMB_W, (THUMB_H * width) / height) * dpr);
      const canvas = await renderPage(doc, index, { width: target });
      let blob: Blob;
      try {
        blob = await canvasToBlob(canvas, 'image/jpeg', 0.82);
      } finally {
        releaseCanvas(canvas);
      }
      if (closed) return;
      const url = URL.createObjectURL(blob);
      urls.push(url);
      onThumb(index, url);
    } catch {
      // 关闭文件时进行中的渲染会被打断，不算失败
      if (!closed) onThumb(index, null);
    }
  }

  function pump() {
    while (active < 2 && waiting.length && !closed) {
      const index = waiting.shift()!;
      settled.add(index);
      active++;
      void render(index).finally(() => {
        active--;
        pump();
      });
    }
  }

  return {
    pages,
    want(index, show) {
      const count = Math.max(0, (visible.get(index) ?? 0) + (show ? 1 : -1));
      if (count) visible.set(index, count);
      else visible.delete(index);
      if (!count) {
        // 快速滚过的页不再排队，优先渲染眼前的
        waiting = waiting.filter((i) => i !== index);
      } else if (!settled.has(index) && !waiting.includes(index)) {
        waiting.push(index);
        pump();
      }
    },
    async dispose() {
      closed = true;
      waiting = [];
      for (const url of urls) URL.revokeObjectURL(url);
      await closePdf(doc);
    },
  };
}
