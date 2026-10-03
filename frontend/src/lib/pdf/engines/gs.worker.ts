/// <reference lib="webworker" />
// Ghostscript 在独立 worker 里跑：callMain 是同步的，放在主线程会卡住页面。
// 一个 worker 只处理一个任务，主线程拿到结果就 terminate()，释放约 256 MB 的 WASM 内存。
// 这里不能 import 本站其他模块（engines.svelte.ts 依赖 document），gs.js 和 gs.wasm 的网址由主线程算好传进来。

// 这个构建的 FS 只导出了常用方法（没有 analyzePath / mkdirTree）
interface EmFS {
  writeFile(path: string, data: Uint8Array | string): void;
  readFile(path: string): Uint8Array;
  mkdir(path: string): void;
}

interface GsModule {
  callMain(args: string[]): number;
  FS: EmFS;
}

type Factory = (options: object) => Promise<GsModule>;

export interface GsRequest {
  jsUrl: string;
  wasmUrl: string;
  args: string[];
  /** 写入内存文件系统的文件，键为绝对路径 */
  inputs: Record<string, Uint8Array | string>;
  /** 运行结束后读回的文件（绝对路径） */
  outputs: string[];
}

export type GsMessage =
  | { type: 'progress'; page: number; total: number }
  | { type: 'done'; code: number; files: Record<string, Uint8Array>; log: string[] }
  | { type: 'error'; message: string; log: string[] };

const post = (message: GsMessage, transfer: Transferable[] = []) =>
  (self as unknown as DedicatedWorkerGlobalScope).postMessage(message, transfer);

function ensureDir(fs: EmFS, file: string) {
  let dir = '';
  for (const part of file.split('/').slice(1, -1)) {
    dir += '/' + part;
    try {
      fs.mkdir(dir);
    } catch {
      /* 已存在 */
    }
  }
}

self.onmessage = async (event: MessageEvent<GsRequest>) => {
  const { jsUrl, wasmUrl, args, inputs, outputs } = event.data;
  const log: string[] = [];
  let first = 1;
  let total = 0;
  try {
    const { default: create } = (await import(/* @vite-ignore */ jsUrl)) as { default: Factory };
    const gs = await create({
      locateFile: () => wasmUrl,
      // 进度来自 Ghostscript 的输出：先打印 “Processing pages 1 through N.”，之后每页一行 “Page k”
      print: (line: string) => {
        log.push(line);
        const range = /^Processing pages (\d+) through (\d+)/.exec(line);
        if (range) {
          first = Number(range[1]);
          total = Number(range[2]) - first + 1;
        }
        const page = /^Page (\d+)$/.exec(line);
        if (page && total) post({ type: 'progress', page: Number(page[1]) - first + 1, total });
      },
      printErr: (line: string) => log.push(line),
    });
    for (const [path, data] of Object.entries(inputs)) {
      ensureDir(gs.FS, path);
      gs.FS.writeFile(path, data);
    }
    let code: number;
    try {
      code = gs.callMain([...args]);
    } catch (e) {
      // exit() 以异常形式抛出退出码；其余是 abort / 内存不足
      const status = (e as { status?: unknown })?.status;
      code = typeof status === 'number' ? status : -1;
      if (code === -1) log.push(String(e));
    }
    const files: Record<string, Uint8Array> = {};
    for (const path of outputs) {
      try {
        files[path] = gs.FS.readFile(path);
      } catch {
        /* 没有生成 */
      }
    }
    post({ type: 'done', code, files, log }, Object.values(files).map((f) => f.buffer as ArrayBuffer));
  } catch (e) {
    post({ type: 'error', message: e instanceof Error ? e.message : String(e), log });
  }
};
