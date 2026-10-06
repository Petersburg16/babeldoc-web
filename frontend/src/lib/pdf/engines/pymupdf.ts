// PyMuPDF（Pyodide + WASM）：PDF 转 Word 等需要 Python 生态的处理。运行时与 wheel 由本站 /pdf-assets/ 提供，
// 页面先用 engines.ensure 下载进 Cache Storage，worker 再从那里读，不碰任何第三方 CDN。
// 移植自 BentoPDF（AGPL-3.0）对 @bentopdf/pymupdf-wasm 的用法，按本站引擎与缓存方式重写。
// 一次只跑一个任务；WASM 内存只增不减（跑完一份论文约 250 MB），空闲一分钟后关掉 worker。
import { absoluteAssetUrl, CACHE_NAME, type EngineId, engineFiles } from '../engines.svelte';
import { Cancelled } from '../input';
import type { PyRequest, PyResponse } from './pymupdf.worker';

const IDLE_MS = 60_000;

export interface PyProgress {
  /** init 为加载引擎，其余由各个 Python 入口函数自己定义 */
  stage: string;
  done: number;
  total: number;
}

export interface PyJob {
  /** 除 PyMuPDF 本身以外还要安装的 wheel（完整网址，见 engineWheels） */
  wheels: string[];
  setup: string;
  entry: string;
  input: Uint8Array;
  options?: unknown;
}

export class PymupdfError extends Error {
  constructor(
    message: string,
    /** Python 异常类名；UserError 表示入口函数给出的中文说明 */
    public pyType = '',
  ) {
    super(message);
  }
}

/** 某个引擎目录下的全部 wheel（完整网址），文件名取自锁文件，升级 wheel 不用改代码 */
export function engineWheels(id: EngineId) {
  return engineFiles(id)
    .filter((name) => name.endsWith('.whl'))
    .map((name) => absoluteAssetUrl(id, name));
}

let worker: Worker | null = null;
let idleTimer: ReturnType<typeof setTimeout> | undefined;
let seq = 0;
let chain: Promise<unknown> = Promise.resolve();

function toError(msg: Extract<PyResponse, { type: 'error' }>) {
  if (msg.pyType === 'UserError') return new PymupdfError(msg.message, msg.pyType);
  if (msg.pyType === 'MemoryError' || /out of memory|memory access out of bounds|Aborted\(/i.test(msg.message)) {
    return new PymupdfError('内存不足：文件太大或页数太多，可以只转换部分页再试', msg.pyType);
  }
  if (msg.pyType) return new PymupdfError(`处理失败：${msg.message}`, msg.pyType);
  return new PymupdfError(`PDF 解析引擎加载失败：${msg.message}`);
}

function exec(job: PyJob, onProgress?: (p: PyProgress) => void, signal?: AbortSignal) {
  // 排队期间调用方已离开：不碰 worker，也不清空闲计时
  if (signal?.aborted) return Promise.reject(new Cancelled());
  clearTimeout(idleTimer);
  return new Promise<{ data: Uint8Array; meta: string }>((resolve, reject) => {
    const w = (worker ??= new Worker(new URL('./pymupdf.worker.ts', import.meta.url), { type: 'module', name: 'pymupdf' }));
    const id = ++seq;
    const kill = () => {
      w.terminate();
      if (worker === w) worker = null;
    };
    const finish = () => {
      w.removeEventListener('message', onMessage);
      w.removeEventListener('error', onCrash);
      w.removeEventListener('messageerror', onCrash);
      signal?.removeEventListener('abort', onAbort);
    };
    // Python 跑起来就停不下：中止只能关掉 worker，下一个任务重新启动引擎（从缓存读，几秒）
    const onAbort = () => {
      finish();
      kill();
      reject(new Cancelled());
    };
    const onMessage = (e: MessageEvent<PyResponse>) => {
      const msg = e.data;
      if (msg.id !== id) return;
      if (msg.type === 'progress') {
        onProgress?.({ stage: msg.stage, done: msg.done, total: msg.total });
        return;
      }
      finish();
      if (msg.type === 'error' && msg.fatal) kill();
      else idleTimer = setTimeout(kill, IDLE_MS);
      if (msg.type === 'done') resolve({ data: new Uint8Array(msg.data), meta: msg.meta });
      else reject(toError(msg));
    };
    const onCrash = (e: Event) => {
      finish();
      kill();
      const detail = e instanceof ErrorEvent && e.message ? `（${e.message}）` : '';
      reject(new PymupdfError(`PDF 解析引擎意外退出${detail}，可能是内存不足，可以只转换部分页再试`));
    };
    w.addEventListener('message', onMessage);
    w.addEventListener('error', onCrash);
    w.addEventListener('messageerror', onCrash);
    signal?.addEventListener('abort', onAbort, { once: true });
    const input = job.input.slice().buffer as ArrayBuffer; // 转交给 worker，调用方的数据不受影响
    const request: PyRequest = {
      id,
      indexURL: absoluteAssetUrl('pymupdf'),
      wheels: [...engineWheels('pymupdf'), ...job.wheels],
      cacheName: CACHE_NAME,
      setup: job.setup,
      entry: job.entry,
      input,
      options: JSON.stringify(job.options ?? {}),
    };
    w.postMessage(request, [input]);
  });
}

/**
 * 在 Pyodide 里跑一个入口函数，返回它写出的文件和它返回的说明（JSON 字符串）。任务按调用顺序排队；
 * 工具页卸载时用 signal 中止，否则离开页面后任务还在后台跑，回来再转换要排在它后面干等
 */
export function runPymupdf(job: PyJob, onProgress?: (p: PyProgress) => void, signal?: AbortSignal) {
  const run = chain.then(() => exec(job, onProgress, signal));
  chain = run.catch(() => {});
  return run;
}
