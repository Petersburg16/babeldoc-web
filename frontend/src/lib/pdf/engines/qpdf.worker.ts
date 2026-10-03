/// <reference lib="webworker" />
// qpdf 在独立 worker 里跑：大文件不卡界面；qpdf-wasm 的输出只走 console，在 worker 里截获不会污染页面控制台。
import createModule from '@neslinesli93/qpdf-wasm';

interface QpdfModule {
  callMain(args: string[]): number;
  FS: {
    writeFile(path: string, data: Uint8Array): void;
    readFile(path: string): Uint8Array;
    readdir(path: string): string[];
    unlink(path: string): void;
  };
}

export interface QpdfRequest {
  id: number;
  wasmUrl: string;
  args: string[];
  inputs: Record<string, Uint8Array>;
  /** 要读回的输出文件名；为 null 时读回命令新建的所有文件（拆分时文件名由 qpdf 决定） */
  outputs: string[] | null;
}

export interface QpdfResponse {
  id: number;
  code: number;
  log: string;
  files: Record<string, Uint8Array>;
}

let sink: string[] | null = null;
const log0 = console.log.bind(console);
const err0 = console.error.bind(console);
console.log = (...a: unknown[]) => (sink ? void sink.push(a.join(' ')) : log0(...a));
console.error = (...a: unknown[]) => (sink ? void sink.push(a.join(' ')) : err0(...a));

let mod: Promise<QpdfModule> | null = null;

self.onmessage = async (event: MessageEvent<QpdfRequest>) => {
  const { id, wasmUrl, args, inputs, outputs } = event.data;
  const files: Record<string, Uint8Array> = {};
  try {
    mod ??= (createModule as unknown as (o: object) => Promise<QpdfModule>)({ locateFile: () => wasmUrl });
    const q = await mod;
    const before = new Set(q.FS.readdir('/'));
    for (const [name, data] of Object.entries(inputs)) q.FS.writeFile('/' + name, data);
    sink = [];
    let code: number;
    try {
      code = q.callMain([...args]);
    } catch (e) {
      code = -1; // 只有 abort / 内存不足会走到这里，模块之后不可再用
      sink.push(String(e));
      mod = null;
    }
    const log = sink.join('\n').replace(/^[^\n:]*?(this\.program|qpdf[^:]*|[\w.-]+\.m?js): /gm, '');
    sink = null;
    const created = q.FS.readdir('/').filter((n) => !before.has(n) && !(n in inputs));
    for (const name of outputs ?? created) {
      try {
        files[name] = q.FS.readFile('/' + name);
      } catch {
        /* 没有生成 */
      }
    }
    for (const name of [...Object.keys(inputs), ...created]) {
      try {
        q.FS.unlink('/' + name);
      } catch {
        /* 已删除 */
      }
    }
    const transfer = Object.values(files).map((f) => f.buffer as ArrayBuffer);
    (self as unknown as Worker).postMessage({ id, code, log, files } satisfies QpdfResponse, transfer);
  } catch (err) {
    sink = null;
    mod = null; // wasm 加载失败：下次重建
    (self as unknown as Worker).postMessage({ id, code: -1, log: String(err), files } satisfies QpdfResponse);
  }
};
