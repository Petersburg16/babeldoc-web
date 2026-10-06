// 读入用户的 PDF：加密的先解开（需要打开密码时弹窗询问），后续工具拿到的都是未加密的字节。
// 知网等来源的 PDF 常带“只限制权限”的加密，不需要密码也要先解开，否则 pdf-lib 会读出空白页。
import { readBytes } from './files';
import { askPassword } from './password.svelte';

export class Cancelled extends Error {
  constructor() {
    super('已取消');
  }
}

export interface UnlockedPdf {
  bytes: Uint8Array;
  /** 原文件是否加密（包括只限制权限的情况） */
  encrypted: boolean;
  /** 用户输入的打开密码（如有），加密工具可以沿用 */
  password?: string;
}

/** 需要 qpdf 引擎（调用方先 ensure 'qpdf'） */
export async function unlockPdf(file: File, bytes?: Uint8Array): Promise<UnlockedPdf> {
  const { encryptionInfo, decrypt, QpdfError } = await import('./engines/qpdf');
  const data = bytes ?? (await readBytes(file));
  const info = await encryptionInfo(data);
  if (!info.encrypted) return { bytes: data, encrypted: false };
  if (!info.needsPassword) return { bytes: await decrypt(data), encrypted: true };
  let incorrect = false;
  for (;;) {
    const password = await askPassword(file.name, incorrect);
    if (password === null) throw new Cancelled();
    try {
      return { bytes: await decrypt(data, password), encrypted: true, password };
    } catch (e) {
      if (e instanceof QpdfError && e.message === '密码不正确') {
        incorrect = true;
        continue;
      }
      throw e;
    }
  }
}

/** 用户的文件读不出来时给出的说明（带文件名）；在界面上自己显示报错的工具也用这一句 */
export function unreadable(file: File) {
  return `无法读取「${file.name}」，文件可能已损坏或不是 PDF`;
}

/**
 * 读入用户选的 PDF：加密的先解开（要密码时弹窗），结果不再加密；countPages 时顺带读页数。
 * qpdf 读不了时换成带文件名的中文说明；引擎崩溃（worker 自己重跑也失败）时请用户重试，不说成文件损坏。
 * 需要 qpdf 引擎（调用方先 ensure 'qpdf'）
 */
export async function readUserPdf(file: File): Promise<UnlockedPdf>;
export async function readUserPdf(file: File, options: { countPages: true }): Promise<UnlockedPdf & { pages: number }>;
export async function readUserPdf(file: File, options: { countPages?: boolean } = {}): Promise<UnlockedPdf & { pages?: number }> {
  const { QpdfError, retryOnCrash } = await import('./engines/qpdf');
  try {
    const unlocked = await unlockPdf(file);
    if (!options.countPages) return unlocked;
    const { countPages } = await import('./ops/pages');
    // 只给读页数套重试：解密那一步会弹密码框，重试会让用户再输一遍
    return { ...unlocked, pages: await retryOnCrash(() => countPages(unlocked.bytes)) };
  } catch (e) {
    if (!(e instanceof QpdfError)) throw e;
    throw new Error(e.code === -1 ? `读取「${file.name}」时 PDF 处理引擎出错，请重试` : unreadable(file), { cause: e });
  }
}

/**
 * 需要打开密码时询问用户，直到密码正确；不需要密码返回 undefined。
 * 给直接用 qpdf 处理原文件的工具用（qpdf 可以带着密码读入，不必先解密）。需要 qpdf 引擎。
 */
export async function passwordFor(file: File, bytes: Uint8Array): Promise<string | undefined> {
  const { encryptionInfo } = await import('./engines/qpdf');
  if (!(await encryptionInfo(bytes)).needsPassword) return undefined;
  let incorrect = false;
  for (;;) {
    const password = await askPassword(file.name, incorrect);
    if (password === null) throw new Cancelled();
    if (!(await encryptionInfo(bytes, password)).needsPassword) return password;
    incorrect = true;
  }
}
