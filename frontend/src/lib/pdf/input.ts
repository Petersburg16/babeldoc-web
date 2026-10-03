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
