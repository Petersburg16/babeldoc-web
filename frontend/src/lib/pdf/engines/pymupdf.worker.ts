/// <reference lib="webworker" />
// Pyodide + PyMuPDF 在独立 worker 里跑：Python 单线程、一跑就是几秒到几十秒，放在页面线程会卡死界面。
// 移植自 BentoPDF（AGPL-3.0）src/js/utils/pymupdf-loader.ts 与 @bentopdf/pymupdf-wasm 的加载流程，按本站引擎与缓存方式重写：
// 不用该包的 dist/index.js（它每次都装全部 wheel，还把用户数据拼进 Python 源码），只用它带的 Pyodide 运行时和 wheel。
// 这里不能 import engines.svelte.ts（runes 模块需要 document），网址都由主线程算好传进来。

export interface PyRequest {
  id: number;
  /** Pyodide 运行时所在目录（绝对网址，以 / 结尾） */
  indexURL: string;
  /** 要安装的 wheel（绝对网址）。按网址安装不会解析依赖，所以要列全 */
  wheels: string[];
  /** 引擎文件所在的 Cache Storage 名称 */
  cacheName: string;
  /** 定义入口函数的 Python 源码，同一个 worker 里只执行一次 */
  setup: string;
  /** 入口函数名：entry(in_path, out_path, opts_json, progress) -> str */
  entry: string;
  input: ArrayBuffer;
  /** JSON 字符串，原样交给入口函数，用户数据不拼进 Python 源码 */
  options: string;
}

export type PyResponse =
  | { id: number; type: 'progress'; stage: string; done: number; total: number }
  | { id: number; type: 'done'; data: ArrayBuffer; meta: string }
  | { id: number; type: 'error'; message: string; pyType: string; fatal: boolean };

interface Pyodide {
  FS: {
    writeFile(path: string, data: Uint8Array): void;
    readFile(path: string): Uint8Array;
    unlink(path: string): void;
  };
  globals: { set(name: string, value: unknown): void; delete(name: string): void };
  runPython(code: string): unknown;
  loadPackage(
    urls: string[],
    options?: { messageCallback?: (msg: string) => void; errorCallback?: (msg: string) => void },
  ): Promise<unknown>;
}

type LoadPyodide = (options: {
  indexURL: string;
  stdout?: (text: string) => void;
  stderr?: (text: string) => void;
}) => Promise<Pyodide>;

// Pyodide 用 fetch() 取 wasm、标准库、锁文件和 wheel：先从页面已下载好的 Cache Storage 里取，
// 站外网址一律拒绝（Pyodide 在 Node 下才会回退到 jsDelivr，这里再兜一层）。
let cacheName = '';
const realFetch = self.fetch.bind(self);
self.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = new URL(input instanceof Request ? input.url : String(input), self.location.href);
  if (url.origin !== self.location.origin) throw new TypeError(`已拦截站外请求：${url.href}`);
  if (cacheName && url.pathname.startsWith('/pdf-assets/')) {
    try {
      const hit = await (await caches.open(cacheName)).match(url.href, { ignoreVary: true });
      if (hit) return hit;
    } catch {
      /* Cache Storage 不可用（隐私模式等）时走网络 */
    }
  }
  return realFetch(input, init);
}) as typeof fetch;

function withTimeout<T>(job: Promise<T>, ms: number, what: string): Promise<T> {
  // wasm 的 Content-Type 不对时 loadPyodide 会一直挂着不报错，所以一定要有超时
  let timer: ReturnType<typeof setTimeout> | undefined;
  const limit = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what}超时，请刷新页面后重试`)), ms);
  });
  return Promise.race([job, limit]).finally(() => clearTimeout(timer));
}

let runtime: Promise<Pyodide> | null = null;
const installed = new Set<string>();
const prepared = new Set<string>();

function boot(indexURL: string) {
  runtime ??= (async () => {
    const { loadPyodide } = (await import(/* @vite-ignore */ indexURL + 'pyodide.js')) as { loadPyodide: LoadPyodide };
    return withTimeout(
      loadPyodide({ indexURL, stdout: () => {}, stderr: (text) => console.debug('[pymupdf]', text) }),
      120_000,
      '启动 Python 运行时',
    );
  })();
  return runtime;
}

const post = (msg: PyResponse, transfer: Transferable[] = []) => (self as unknown as Worker).postMessage(msg, transfer);

self.onmessage = async (event: MessageEvent<PyRequest>) => {
  const req = event.data;
  const progress = (stage: string, done = 0, total = 0) => post({ id: req.id, type: 'progress', stage, done, total });
  let running = false;
  try {
    if (!/^[A-Za-z_]\w*$/.test(req.entry)) throw new Error(`入口函数名无效：${req.entry}`);
    cacheName = req.cacheName;
    progress('init');
    const py = await boot(req.indexURL);
    const need = req.wheels.filter((url) => !installed.has(url));
    if (need.length) {
      await withTimeout(
        py.loadPackage(need, { messageCallback: () => {}, errorCallback: (msg) => console.warn('[pymupdf]', msg) }),
        180_000,
        '安装转换组件',
      );
      for (const url of need) installed.add(url);
    }
    if (!prepared.has(req.entry)) {
      py.runPython(req.setup);
      prepared.add(req.entry);
    }
    running = true;
    py.FS.writeFile('/in.pdf', new Uint8Array(req.input));
    py.globals.set('bdw_opts', req.options);
    py.globals.set('bdw_progress', progress);
    let meta: string;
    try {
      meta = String(py.runPython(`${req.entry}('/in.pdf', '/out.bin', bdw_opts, bdw_progress)`));
    } finally {
      py.globals.delete('bdw_progress');
      py.globals.delete('bdw_opts');
      try {
        py.FS.unlink('/in.pdf');
      } catch {
        /* 已删除 */
      }
    }
    const out = py.FS.readFile('/out.bin');
    py.FS.unlink('/out.bin');
    const whole = out.byteOffset === 0 && out.byteLength === out.buffer.byteLength;
    const data = (whole ? out.buffer : out.slice().buffer) as ArrayBuffer;
    post({ id: req.id, type: 'done', data, meta }, [data]);
  } catch (e) {
    // PythonError 带 type（异常类名），最后一行是“类名: 信息”；其余（加载失败、wasm 崩溃）之后解释器不可再用
    const pyType = typeof (e as { type?: unknown })?.type === 'string' ? (e as { type: string }).type : '';
    const text = e instanceof Error ? e.message : String(e);
    let message = text;
    if (pyType) {
      const last = text.trim().split('\n').pop() ?? '';
      message = last.startsWith(`${pyType}: `) ? last.slice(pyType.length + 2) : last;
    }
    post({ id: req.id, type: 'error', message, pyType, fatal: !running || !pyType || pyType === 'MemoryError' });
  }
};
