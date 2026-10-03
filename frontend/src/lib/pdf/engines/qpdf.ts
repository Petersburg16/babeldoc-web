// qpdf（WASM）：合并、拆分、提取、整理页面、加解密。比 pdf-lib 重写整份文件更省体积，
// 也会保留文档结构（元数据、名称树、标签结构）；知网那类只限制权限的加密文件也能直接处理。
// 移植自 BentoPDF（AGPL-3.0）对 qpdf-wasm 的用法，按本站引擎与缓存方式重写。
// 命令在独立 worker 里执行，空闲一分钟后关掉 worker，释放 WASM 内存。
import { assetUrl } from '../engines.svelte';
import type { QpdfRequest, QpdfResponse } from './qpdf.worker';

const IDLE_MS = 60_000;

let worker: Worker | null = null;
let idleTimer: ReturnType<typeof setTimeout> | undefined;
let seq = 0;
const pending = new Map<number, (r: QpdfResponse) => void>();

function getWorker() {
  if (!worker) {
    worker = new Worker(new URL('./qpdf.worker.ts', import.meta.url), { type: 'module', name: 'qpdf' });
    worker.onmessage = (e: MessageEvent<QpdfResponse>) => {
      const done = pending.get(e.data.id);
      pending.delete(e.data.id);
      done?.(e.data);
    };
    worker.onerror = (e) => {
      for (const [id, resolve] of pending) resolve({ id, code: -1, log: e.message || 'qpdf 引擎崩溃', files: {} });
      pending.clear();
      worker?.terminate();
      worker = null;
    };
  }
  return worker;
}

export class QpdfError extends Error {
  constructor(
    public code: number,
    public log: string,
  ) {
    super(QpdfError.describe(code, log));
  }

  /** 把 qpdf 的英文输出换成用户看得懂的中文，去掉内存文件系统里的路径（/in.pdf: …） */
  static describe(code: number, log: string) {
    if (/invalid password/i.test(log)) return '密码不正确';
    if (/startxref|not a PDF|unable to find trailer|EOF|file is damaged|can't find PDF header|xref/i.test(log)) {
      return '文件已损坏，或不是有效的 PDF';
    }
    if (code === -1) return 'PDF 处理引擎出错，请重试';
    const detail = (log.trim().split('\n').pop() ?? '').replace(/^\/[\w.-]+:\s*/, '').replace(/^qpdf:\s*/, '');
    return `PDF 处理失败${detail ? `：${detail}` : ''}`;
  }
}

export interface QpdfResult {
  code: number;
  log: string;
  files: Record<string, Uint8Array>;
}

/**
 * 跑一条 qpdf 命令。inputs 以 /<名字> 写入内存文件系统（名字只用 ASCII，用户文件名可能是中文）；
 * outputs 为要读回的文件名，传 null 读回命令新建的全部文件。输入会被复制后转交 worker，调用方的数据不受影响。
 * 退出码 0 为成功，3 为成功但有警告（真实世界的 PDF 很常见），其余抛出 QpdfError。
 * 注意：qpdf 的日志对象在同一模块实例里是全局的，一旦有命令往标准输出打印过（如 --show-npages），
 * 之后再把 --json 输出到标准输出会报错；要 JSON 时写到文件（--json=2 ... /o.json）再读回。
 */
export function runQpdf(
  args: string[],
  inputs: Record<string, Uint8Array> = {},
  outputs: string[] | null = ['out.pdf'],
): Promise<QpdfResult> {
  clearTimeout(idleTimer);
  const id = ++seq;
  const copies = Object.fromEntries(Object.entries(inputs).map(([k, v]) => [k, v.slice()]));
  return new Promise<QpdfResponse>((resolve) => {
    pending.set(id, resolve);
    const request: QpdfRequest = { id, wasmUrl: assetUrl('qpdf', 'qpdf.wasm'), args, inputs: copies, outputs };
    getWorker().postMessage(request, Object.values(copies).map((c) => c.buffer as ArrayBuffer));
  }).then((r) => {
    if (pending.size === 0) {
      idleTimer = setTimeout(() => {
        worker?.terminate();
        worker = null;
      }, IDLE_MS);
    }
    if (r.code !== 0 && r.code !== 3) throw new QpdfError(r.code, r.log);
    return r;
  });
}

/** 单输入单输出：qpdfOne(['--decrypt'], bytes, password) */
export async function qpdfOne(args: string[], input: Uint8Array, password?: string) {
  const pw = password ? [`--password=${password}`] : [];
  const r = await runQpdf([...pw, ...args, '/in.pdf', '/out.pdf'], { 'in.pdf': input });
  const out = r.files['out.pdf'];
  if (!out) throw new QpdfError(r.code, r.log || '没有生成文件');
  return out;
}

export interface EncryptionInfo {
  encrypted: boolean;
  /** 打开时需要密码（只限制打印/复制的文件不需要） */
  needsPassword: boolean;
}

/** 检查加密状态；password 用于验证用户输入的密码 */
export async function encryptionInfo(input: Uint8Array, password?: string): Promise<EncryptionInfo> {
  const pw = password ? [`--password=${password}`] : [];
  try {
    const r = await runQpdf([...pw, '--show-encryption', '/in.pdf'], { 'in.pdf': input }, []);
    return { encrypted: !/not encrypted/i.test(r.log), needsPassword: false };
  } catch (e) {
    if (e instanceof QpdfError && /invalid password/i.test(e.log)) return { encrypted: true, needsPassword: true };
    throw e;
  }
}

/** 去掉加密（含只限制权限的情况）；需要打开密码时传 password，密码错误抛出 QpdfError('密码不正确') */
export function decrypt(input: Uint8Array, password?: string) {
  return qpdfOne(['--decrypt'], input, password);
}
