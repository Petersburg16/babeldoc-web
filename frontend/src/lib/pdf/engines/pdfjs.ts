// PDF.js：缩略图、页面转图片、页数与密码探测。只有用到的工具会 import 这个模块。
// 用 legacy 构建：常规构建依赖 Math.sumPrecise 等很新的 API（Chrome 145+），国内浏览器套壳跟不上；
// legacy 5.7 支持 Chrome 118+。CMap、标准字体、wasm 解码器都由本站提供，缺了 CMap 知网等中文 PDF 会丢字。
import * as pdfjs from 'pdfjs-dist/legacy/build/pdf.mjs';
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url';
import { assetBase } from '../engines.svelte';

export type PdfDoc = pdfjs.PDFDocumentProxy;

// 所有文档共用一个显式创建的 worker：只设 workerPort 时，第一个文档销毁会连带关掉共享 worker
let worker: pdfjs.PDFWorker | null = null;

function sharedWorker() {
  if (worker && !worker.destroyed) return worker;
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
  worker = pdfjs.PDFWorker.create({ port: new Worker(workerUrl, { type: 'module' }) });
  return worker;
}

export class PasswordNeeded extends Error {
  constructor(public incorrect: boolean) {
    super(incorrect ? '密码不正确' : '这个 PDF 需要打开密码');
  }
}

/** 打开 PDF；需要密码时抛出 PasswordNeeded。用完调用 closePdf。 */
export async function openPdf(bytes: Uint8Array, password?: string): Promise<PdfDoc> {
  // 四个数据地址都要是绝对网址且以 / 结尾，PDF.js 才会在 worker 里自己取数据（ICC 色彩管理也只在这种模式下启用）
  const base = new URL(assetBase('render'), location.origin).href;
  const task = pdfjs.getDocument({
    worker: sharedWorker(),
    data: bytes.slice(), // 会被转移给 worker，复制一份免得调用方的数据失效
    password,
    cMapUrl: base + 'cmaps/',
    cMapPacked: true,
    standardFontDataUrl: base + 'standard_fonts/',
    wasmUrl: base + 'wasm/',
    iccUrl: base + 'iccs/',
    enableXfa: false,
  });
  try {
    return await task.promise;
  } catch (e) {
    await task.destroy();
    const err = e as { name?: string; code?: number };
    if (err?.name === 'PasswordException') {
      throw new PasswordNeeded(err.code === pdfjs.PasswordResponses.INCORRECT_PASSWORD);
    }
    throw new Error(`无法读取这个 PDF：${e instanceof Error ? e.message : String(e)}`);
  }
}

export async function closePdf(doc: PdfDoc | null | undefined) {
  if (doc) await doc.loadingTask.destroy();
}

/** 页数；需要密码时抛出 PasswordNeeded */
export async function pageCount(bytes: Uint8Array, password?: string) {
  const doc = await openPdf(bytes, password);
  try {
    return doc.numPages;
  } finally {
    await closePdf(doc);
  }
}

// 画布像素上限，参照 PDF.js 自带阅读器：桌面 2^25，手机端更小
const MAX_PIXELS = /Android|iPhone|iPad/i.test(navigator.userAgent) ? 5_242_880 : 33_554_432;

export interface RenderOptions {
  /** 目标宽度（像素），用于缩略图 */
  width?: number;
  /** 分辨率（dpi），用于导出图片；PDF.js 的 scale 1 = 72 dpi */
  dpi?: number;
  /** 额外旋转角度（在页面自身的 /Rotate 之上） */
  rotation?: number;
  background?: string;
}

/** 把第 index 页（0 起计）画到新画布上；调用方用完后把 canvas 宽高设为 0 释放内存 */
export async function renderPage(doc: PdfDoc, index: number, opts: RenderOptions = {}) {
  const page = await doc.getPage(index + 1);
  try {
    const rotation = ((page.rotate + (opts.rotation ?? 0)) % 360 + 360) % 360;
    const base = page.getViewport({ scale: 1, rotation });
    let scale = opts.width ? opts.width / base.width : (opts.dpi ?? 144) / 72;
    if (base.width * base.height * scale * scale > MAX_PIXELS) {
      scale = Math.sqrt(MAX_PIXELS / (base.width * base.height));
    }
    const viewport = page.getViewport({ scale, rotation });
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.floor(viewport.width));
    canvas.height = Math.max(1, Math.floor(viewport.height));
    const ctx = canvas.getContext('2d', { alpha: false })!;
    ctx.fillStyle = opts.background ?? '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvas, canvasContext: ctx, viewport }).promise;
    return canvas;
  } finally {
    page.cleanup();
  }
}

export function canvasToBlob(canvas: HTMLCanvasElement, type: 'image/png' | 'image/jpeg' | 'image/webp', quality?: number) {
  return new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('图片生成失败，可能是页面太大'))), type, quality),
  );
}

export function releaseCanvas(canvas: HTMLCanvasElement) {
  canvas.width = 0;
  canvas.height = 0;
}

export { pdfjs };
