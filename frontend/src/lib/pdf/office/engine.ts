// LibreOffice（WASM，@matbee/libreoffice-converter 2.7.2）：Word、Excel、PowerPoint 等转 PDF。
// 移植自 BentoPDF（AGPL-3.0）src/js/utils/libreoffice-loader.ts，按本站引擎与界面重写。
// 运行文件都来自本站 /pdf-assets/libreoffice/…，库的默认路径（/wasm/、/dist/）一律不用。
// soffice.wasm/.data 由 engines.ensure 存进 Cache Storage 后以 blob URL 交给 worker：
// worker 自带 120 秒初始化超时，直接给网址时慢速下载也算在里面，首次使用容易超时。
// 字体只能在启动时装入：每个标签页只留一个实例，后面的文档要更多字体就按并集重启；
// 一个实例同时只能转一个文件，所以全部串行；空闲几分钟后关掉，释放约 1.5 GB 内存。
import type { FontData, InputFormat, WorkerBrowserConverter } from '@matbee/libreoffice-converter/browser';
import { errorText } from '../../format';
import { assetBlobUrl, assetBytes, assetUrl, engines, type Progress } from '../engines.svelte';
import { Cancelled } from '../input';
import fcLocalConf from './fc_local.conf?raw';
import { fontEngineIds, fontFiles, type FontKey } from './fonts';

const IDLE_MS = 3 * 60_000;
const INIT_TIMEOUT_MS = 180_000;

interface Instance {
  conv: WorkerBrowserConverter;
  keys: Set<FontKey>;
  /** worker 崩溃或被关掉时 reject：库本身不会结束已发出的请求，不然会一直卡住 */
  ended: Promise<never>;
  end: (reason: Error) => void;
}

let current: Instance | null = null;
/** 正在初始化、还不能用的实例：离开页面时也要能关掉 */
let starting: Instance | null = null;
let chain: Promise<unknown> = Promise.resolve();
let active = 0;
let idleTimer: ReturnType<typeof setTimeout> | undefined;
let releaseWhenIdle = false;
let onPercent: ((percent: number) => void) | null = null;

export class OfficeCrash extends Error {}

/** 当前浏览器不能运行时返回原因（引擎要用 SharedArrayBuffer 和多线程，必须跨源隔离） */
export function officeUnsupported() {
  if (!globalThis.crossOriginIsolated || typeof SharedArrayBuffer === 'undefined' || typeof WebAssembly === 'undefined') {
    return '当前浏览器无法运行转换引擎，请用电脑上的 Chrome、Edge 或 Firefox 最新版打开';
  }
  return '';
}

/** 串行执行：按字体重启引擎时也不会打断别的转换 */
export function exclusive<T>(fn: () => Promise<T>): Promise<T> {
  clearTimeout(idleTimer);
  // 离开后又回到工具页并开始新的转换，按正常的空闲时间释放
  releaseWhenIdle = false;
  active += 1;
  const job = chain.then(fn);
  chain = job.catch(() => undefined);
  return job.finally(() => {
    active -= 1;
    if (active === 0) {
      idleTimer = setTimeout(stopOffice, releaseWhenIdle ? 0 : IDLE_MS);
      releaseWhenIdle = false;
    }
  });
}

/** 离开工具页时调用：立即关掉引擎，正在进行的启动、转换以 Cancelled 结束 */
export function releaseOffice() {
  // 还有任务没结束时，等它结束后再关一次，以防它在收到取消前又启动了引擎
  if (active > 0) releaseWhenIdle = true;
  clearTimeout(idleTimer);
  stopOffice();
}

export interface StartHooks {
  /** 字体下载进度（已缓存时也会回调一次） */
  onFonts?: (p: Progress) => void;
  /** 开始启动 LibreOffice */
  onStart?: () => void;
  /** 已离开页面时不再启动 */
  signal?: AbortSignal;
}

/** 确保引擎已装好这些字体并启动；字体不够时按并集重启。需在 exclusive 里调用 */
export async function startOffice(keys: Set<FontKey>, hooks: StartHooks = {}) {
  if (current && [...keys].every((k) => current!.keys.has(k))) return;
  const reason = officeUnsupported();
  if (reason) throw new Error(reason);
  const union = new Set<FontKey>([...(current?.keys ?? []), ...keys]);
  // 字体下载不跟着取消：别的工具可能在等同一个文件，下完的留在缓存里下次用
  if (union.size) await engines.ensure(fontEngineIds(union), hooks.onFonts);
  throwIfAborted(hooks.signal);
  stopOffice();
  hooks.onStart?.();

  const [{ WorkerBrowserConverter }, fonts] = await Promise.all([
    import('@matbee/libreoffice-converter/browser'),
    loadFonts(union),
  ]);
  let wasmUrl = '';
  let dataUrl = '';
  let inst: Instance | null = null;
  try {
    wasmUrl = await assetBlobUrl('libreoffice', 'soffice.wasm');
    dataUrl = await assetBlobUrl('libreoffice', 'soffice.data');
    // 最后一次 await 之后、创建 worker 之前：从这里起实例都登记在 starting/current 上，releaseOffice 关得掉
    throwIfAborted(hooks.signal);
    let end!: (reason: Error) => void;
    const ended = new Promise<never>((_, reject) => (end = reject));
    ended.catch(() => undefined);
    const conv = new WorkerBrowserConverter({
      sofficeJs: assetUrl('libreoffice', 'soffice.js'),
      sofficeWasm: wasmUrl,
      sofficeData: dataUrl,
      sofficeWorkerJs: assetUrl('libreoffice', 'soffice.worker.js'),
      browserWorkerJs: assetUrl('libreoffice', 'browser.worker.global.js'),
      fonts,
      verbose: false,
      onProgress: (p) => onPercent?.(p.percent),
      onError: (e) => {
        end(new OfficeCrash(e.message));
        // 空闲时崩溃也要丢掉这个实例，下一个文件会重新启动
        if (current === inst) stopOffice();
      },
    });
    inst = { conv, keys: union, ended, end };
    starting = inst;
    await withTimeout(Promise.race([conv.initialize(), ended]), INIT_TIMEOUT_MS);
    // 初始化刚完成时被关掉（离开页面）：这个实例已不能用
    if (starting !== inst) throw new Cancelled();
  } catch (e) {
    if (inst) destroy(inst.conv);
    if (e instanceof Cancelled) throw e;
    console.error(e);
    const message = errorText(e);
    if (e instanceof TimeoutError || /timeout/i.test(message)) throw new Error('转换引擎启动超时，请刷新页面后重试');
    throw new Error('转换引擎启动失败，可能是内存不足。请关闭其他标签页，刷新页面后重试');
  } finally {
    if (starting === inst) starting = null;
    // worker 已经读完这两个文件
    if (wasmUrl) URL.revokeObjectURL(wasmUrl);
    if (dataUrl) URL.revokeObjectURL(dataUrl);
  }
  // 库会一直留着这份字体（几十 MB），worker 里已有副本，主线程这份可以丢掉
  (inst.conv as unknown as { options: { fonts?: FontData[] } }).options.fonts = undefined;
  current = inst;
}

export interface ConvertOptions {
  pdfa?: boolean;
}

/** 转一个文件为 PDF；onProgress 报告 0–1。需在 exclusive 里、startOffice 之后调用 */
export async function convertOffice(
  bytes: Uint8Array,
  format: InputFormat,
  options: ConvertOptions = {},
  onProgress?: (fraction: number) => void,
) {
  const inst = current;
  if (!inst) throw new Error('转换引擎没有启动');
  onPercent = (p) => onProgress?.(Math.min(1, Math.max(0, p / 100)));
  try {
    // 文件名只用来告诉 worker 扩展名（决定导入过滤器），用户的文件名可能带中文或奇怪的点号
    const result = await Promise.race([
      inst.conv.convert(
        bytes,
        { outputFormat: 'pdf', inputFormat: format, pdf: options.pdfa ? { pdfaLevel: 'PDF/A-2b' } : undefined },
        `document.${format}`,
      ),
      inst.ended,
    ]);
    return result.data;
  } catch (e) {
    if (e instanceof OfficeCrash && current === inst) stopOffice();
    throw e;
  } finally {
    onPercent = null;
  }
}

async function loadFonts(keys: Set<FontKey>): Promise<FontData[]> {
  if (!keys.size) return [];
  const fonts: FontData[] = await Promise.all(
    fontFiles(keys).map(async ({ id, file }) => ({ filename: file, data: await assetBytes(id, file) })),
  );
  // 覆盖 LibreOffice 字体目录里的 fc_local.conf（它会读这个文件）：保留原有规则，再把宋体、黑体、楷体、仿宋等映射到开源字体
  fonts.push({ filename: 'fc_local.conf', data: new TextEncoder().encode(fcLocalConf) });
  return fonts;
}

/** 关掉引擎（含正在启动的）；它手上未完成的请求以 Cancelled 结束 */
function stopOffice() {
  for (const inst of [current, starting]) {
    if (!inst) continue;
    inst.end(new Cancelled());
    destroy(inst.conv);
  }
  current = starting = null;
}

function throwIfAborted(signal?: AbortSignal) {
  if (signal?.aborted) throw new Cancelled();
}

function destroy(conv: WorkerBrowserConverter) {
  // 不用库的 destroy()：它让 worker 里的 LibreOffice 正常退出，这个构建退出时会 abort（实测报 PThread 未导出），
  // worker 卡死时还会一直等回话。直接结束 worker，它创建的线程 worker 随之结束，内存一并释放
  (conv as unknown as { worker: Worker | null }).worker?.terminate();
}

class TimeoutError extends Error {}

function withTimeout<T>(job: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new TimeoutError()), ms);
  });
  return Promise.race([job, timeout]).finally(() => clearTimeout(timer));
}
