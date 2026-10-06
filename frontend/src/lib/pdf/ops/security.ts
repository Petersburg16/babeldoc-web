// 加密、解密与解除权限限制，全部交给 qpdf。
// 移植自 BentoPDF（AGPL-3.0）src/js/logic/encrypt-pdf-page.ts、remove-restrictions-page.ts，按本站引擎与界面重写。
// 加密状态读 --show-encryption 的文字输出：--json 在常驻的 qpdf 实例里第二次调用就会失败（实测）。
import { QpdfError, qpdfOne, runQpdf, showEncryption } from '../engines/qpdf';

export type PrintLevel = 'full' | 'low' | 'none';

/** 加密工具界面上的四项权限 */
export interface Permissions {
  print: PrintLevel;
  copy: boolean;
  /** 修改内容与增删、调整页面 */
  modify: boolean;
  /** 添加注释与填写表单 */
  annotate: boolean;
}

/** PDF 里逐项的权限位（qpdf 的说法），true 为允许 */
interface Flags {
  printHigh: boolean;
  printLow: boolean;
  copy: boolean;
  modifyOther: boolean;
  assemble: boolean;
  annotate: boolean;
  form: boolean;
}

function toFlags(p: Permissions): Flags {
  return {
    printHigh: p.print === 'full',
    printLow: p.print !== 'none',
    copy: p.copy,
    modifyOther: p.modify,
    assemble: p.modify,
    annotate: p.annotate,
    form: p.annotate,
  };
}

/** 被禁止的操作，按界面用语列出 */
function flagLabels(f: Flags) {
  const out: string[] = [];
  if (!f.printLow) out.push('打印');
  else if (!f.printHigh) out.push('高质量打印');
  if (!f.copy) out.push('复制文字');
  if (!f.modifyOther) out.push('修改内容');
  if (!f.assemble) out.push('调整页面');
  if (!f.annotate) out.push('添加注释');
  if (!f.form) out.push('填写表单');
  return out;
}

export function limitLabels(p: Permissions) {
  return flagLabels(toFlags(p));
}

export function isRestricted(p: Permissions) {
  return limitLabels(p).length > 0;
}

/** AES-256（R6）的密码按 UTF-8 最多取 127 字节，超出部分各阅读器会截掉，不如直接拦下 */
export function passwordTooLong(password: string) {
  return new TextEncoder().encode(password).length > 127;
}

// 去掉 0/O、1/l/I 这类容易抄错的字符；20 位约 116 bit
const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';

export function randomPassword(length = 20) {
  let out = '';
  const limit = 256 - (256 % ALPHABET.length); // 拒绝采样，避免取模偏差
  while (out.length < length) {
    for (const b of crypto.getRandomValues(new Uint8Array(length * 2))) {
      if (b < limit && out.length < length) out += ALPHABET[b % ALPHABET.length];
    }
  }
  return out;
}

export interface EncryptOptions {
  /** 打开密码，空字符串表示不需要密码就能打开（只限制权限） */
  userPassword: string;
  /** 权限密码，留空时：有限制则随机生成，没有限制则与打开密码相同 */
  ownerPassword?: string;
  permissions: Permissions;
}

export interface Encrypted {
  bytes: Uint8Array;
  ownerPassword: string;
  /** 权限密码是自动生成的，需要告诉用户 */
  generated: boolean;
}

/** AES-256 加密。输入须未加密（先用 unlockPdf 解开） */
export async function encryptPdf(input: Uint8Array, o: EncryptOptions): Promise<Encrypted> {
  const restricted = isRestricted(o.permissions);
  if (!o.userPassword && !restricted) throw new Error('请设置打开密码，或至少限制一项权限');
  // qpdf 不接受“有打开密码、权限密码为空”；权限密码与打开密码相同时限制形同虚设，所以有限制就生成随机密码
  const generated = !o.ownerPassword && restricted;
  const owner = o.ownerPassword || (generated ? randomPassword() : o.userPassword);
  const f = toFlags(o.permissions);
  const yn = (b: boolean) => (b ? 'y' : 'n');
  // 用 --user-password= 这种具名写法：密码以 - 开头时位置参数会被当成选项
  const args = [
    '/in.pdf',
    '--encrypt',
    `--user-password=${o.userPassword}`,
    `--owner-password=${owner}`,
    '--bits=256',
    `--print=${f.printHigh ? 'full' : f.printLow ? 'low' : 'none'}`,
    `--extract=${yn(f.copy)}`,
    `--modify-other=${yn(f.modifyOther)}`,
    `--assemble=${yn(f.assemble)}`,
    `--annotate=${yn(f.annotate)}`,
    `--form=${yn(f.form)}`,
    '--',
    '/out.pdf',
  ];
  const r = await runQpdf(args, { 'in.pdf': input });
  const bytes = r.files['out.pdf'];
  if (!bytes) throw new QpdfError(r.code, r.log || '没有生成文件');
  return { bytes, ownerPassword: owner, generated };
}

/**
 * 去掉加密和权限限制。需要打开密码的文件传 password（打开密码或权限密码都行），密码错误抛出 QpdfError('密码不正确')。
 * --remove-restrictions 同时去掉数字签名带来的修改限制；qpdf 重写文件本身就会让签名失效。
 */
export function removeEncryption(input: Uint8Array, password?: string) {
  return qpdfOne(['--decrypt', '--remove-restrictions'], input, password);
}

export interface EncryptionState {
  encrypted: boolean;
  needsPassword: boolean;
  /** 例如 AES-256；需要密码的文件读不到 */
  method?: string;
  /** 被禁止的操作 */
  limits: string[];
}

const METHODS: Record<string, string> = { AESv3: 'AES-256', AESv2: 'AES-128', RC4: 'RC4' };

/** 读加密状态和权限；password 为空时，只限制权限的文件也能读到权限 */
export async function inspectEncryption(input: Uint8Array, password?: string): Promise<EncryptionState> {
  const log = await showEncryption(input, password);
  if (log === null) return { encrypted: true, needsPassword: true, limits: [] };
  if (/not encrypted/i.test(log)) return { encrypted: false, needsPassword: false, limits: [] };
  const allowed = (key: string) => !new RegExp(`^${key}: not allowed`, 'im').test(log);
  const method = /^file encryption method: (\S+)/im.exec(log)?.[1];
  const revision = Number(/^R = (\d+)/m.exec(log)?.[1]);
  return {
    encrypted: true,
    needsPassword: false,
    method: method ? (METHODS[method] ?? method) : revision <= 3 ? 'RC4' : undefined,
    limits: flagLabels({
      printHigh: allowed('print high resolution'),
      printLow: allowed('print low resolution'),
      copy: allowed('extract for any purpose'),
      modifyOther: allowed('modify other'),
      assemble: allowed('modify document assembly'),
      annotate: allowed('modify annotations'),
      form: allowed('modify forms'),
    }),
  };
}
