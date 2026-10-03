// 转换前先看一眼文档：有没有中文、有没有点名楷体或仿宋，据此决定给 LibreOffice 装哪些中文字体
// （字体只能在引擎启动时装入，装多了下载慢、占内存，装少了中文会变成方框）。
// 顺带挡掉设了打开密码的文件：这个 LibreOffice 构建打不开加密文档，给了密码也不行（实测）。
import { unzipSync } from 'fflate';
import { ALL_FONT_KEYS, type FontKey } from './fonts';
import { decodeText } from './text';

export interface OfficeInfo {
  fonts: Set<FontKey>;
  /** 不能转换的原因（已加密、文件损坏），可直接展示给用户 */
  problem?: string;
}

const ZIP_FORMATS: Record<string, string> = {
  docx: 'Word',
  xlsx: 'Excel',
  pptx: 'PowerPoint',
  odt: 'OpenDocument',
  ods: 'OpenDocument',
  odp: 'OpenDocument',
};
const TEXT_FORMATS = new Set(['txt', 'csv']);

const CJK = /[⺀-鿿가-힯豈-﫿︰-﹏＀-￯\u{20000}-\u{3134f}]/u;
// 文字节点（> 与 < 之间）里有中日韩字符
const CJK_TEXT = /[>][^<]*[⺀-鿿가-힯豈-﫿︰-﹏＀-￯\u{20000}-\u{3134f}]/u;
// 标签里（字体名都写在属性上）出现楷体、仿宋；正文里写着“楷体”两个字不算
const KAI_TAG = /<[^>]*?(?:楷|kaiti)/i;
const FANG_TAG = /<[^>]*?(?:仿宋|fangsong)/i;
const KAI = /楷|kaiti/i;
const FANG = /仿宋|fangsong/i;

// 有可见文字的部件（页眉页脚、图表、SmartArt、母版上的文字也算）
const TEXT_PART =
  /^(?:word\/(?:document|header\d*|footer\d*|footnotes|endnotes)\.xml|word\/(?:charts|diagrams)\/[^/]+\.xml|xl\/(?:sharedStrings|worksheets\/[^/]+|charts\/[^/]+|drawings\/[^/]+)\.xml|ppt\/(?:slides|slideLayouts|slideMasters|charts|diagrams)\/[^/]+\.xml|(?:content|styles)\.xml)$/;
// 只写字体、不含正文的部件
const STYLE_PART = /^(?:word\/(?:fontTable|styles|numbering)\.xml|xl\/styles\.xml)$/;
// 主题：每个 Office 主题都给中文脚本列了宋体、等线，整份扫描会把英文文档也判成用了中文字体，只看实际指定的东亚字体
const THEME_PART = /^(?:word|xl|ppt)\/theme\/theme\d*\.xml$/;
const ODF_MANIFEST = 'META-INF/manifest.xml';

/** 看文档需要哪些中文字体，以及能不能转换。name 只用来取扩展名和写提示 */
export function inspectOffice(name: string, bytes: Uint8Array): OfficeInfo {
  const ext = name.slice(name.lastIndexOf('.') + 1).toLowerCase();
  if (!bytes.length) return { fonts: new Set(), problem: `「${name}」是空文件` };
  if (TEXT_FORMATS.has(ext)) return { fonts: plainTextFonts(bytes) };
  const kind = ZIP_FORMATS[ext];
  if (!kind) return { fonts: new Set(ALL_FONT_KEYS) }; // doc/xls/ppt/rtf 等二进制或转义过的格式，没法便宜地判断，全装

  if (isCfb(bytes)) {
    // OOXML 加密后是 OLE 复合文档；不含加密流的则是改了扩展名的旧版 .doc/.xls/.ppt，交给 LibreOffice 识别
    if (includesUtf16(bytes, 'EncryptionInfo')) return { fonts: new Set(), problem: encrypted(name, ext) };
    return { fonts: new Set(ALL_FONT_KEYS) };
  }
  if (bytes[0] !== 0x50 || bytes[1] !== 0x4b) {
    // 不检查的话 LibreOffice 会把乱码当纯文本导入，生成一份满是乱码的 PDF
    return { fonts: new Set(), problem: `「${name}」不是有效的 ${kind} 文件，可能已损坏或扩展名不对` };
  }

  let parts: Record<string, Uint8Array>;
  try {
    parts = unzipSync(bytes, {
      filter: (f) => f.name === ODF_MANIFEST || TEXT_PART.test(f.name) || STYLE_PART.test(f.name) || THEME_PART.test(f.name),
    });
  } catch {
    return { fonts: new Set(ALL_FONT_KEYS) }; // 压缩包有问题，LibreOffice 也许还能修复着打开
  }

  const decoder = new TextDecoder();
  if (parts[ODF_MANIFEST] && decoder.decode(parts[ODF_MANIFEST]).includes('encryption-data')) {
    return { fonts: new Set(), problem: encrypted(name, ext) };
  }

  let cjk = false;
  let kai = false;
  let fang = false;
  for (const [part, data] of Object.entries(parts)) {
    if (cjk && kai && fang) break;
    const isText = TEXT_PART.test(part);
    const isTheme = THEME_PART.test(part);
    if (!isText && !STYLE_PART.test(part) && !isTheme) continue;
    // openpyxl 等工具把中文写成 &#39118; 这样的字符引用，先还原
    const xml = decodeCharRefs(decoder.decode(data));
    if (isTheme) {
      for (const font of themeEastAsianFonts(xml)) {
        kai ||= KAI.test(font);
        fang ||= FANG.test(font);
      }
      continue;
    }
    if (isText) cjk ||= CJK_TEXT.test(xml);
    kai ||= KAI_TAG.test(xml);
    fang ||= FANG_TAG.test(xml);
  }

  const fonts = new Set<FontKey>();
  if (cjk) {
    fonts.add('cjk');
    if (kai) fonts.add('kai');
    if (fang) fonts.add('fang');
  }
  return { fonts };
}

function plainTextFonts(bytes: Uint8Array): Set<FontKey> {
  return new Set(CJK.test(decodeText(bytes).text) ? ['cjk'] : []);
}

function encrypted(name: string, ext: string) {
  const app = ZIP_FORMATS[ext] === 'OpenDocument' ? 'LibreOffice' : ZIP_FORMATS[ext];
  return `「${name}」设置了打开密码，浏览器里无法打开。请先在 ${app} 或 WPS 里取消密码，再重新转换`;
}

function themeEastAsianFonts(xml: string) {
  const fonts: string[] = [];
  for (const m of xml.matchAll(/<a:(ea|font)\b([^>]*)>/g)) {
    if (m[1] === 'font' && !/script="Hans"/.test(m[2])) continue;
    const face = /typeface="([^"]*)"/.exec(m[2])?.[1];
    if (face) fonts.push(face);
  }
  return fonts;
}

function decodeCharRefs(xml: string) {
  if (!xml.includes('&#')) return xml;
  return xml.replace(/&#(x[0-9a-f]+|\d+);/gi, (ref, n: string) => {
    const code = n[0] === 'x' || n[0] === 'X' ? Number.parseInt(n.slice(1), 16) : Number(n);
    return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : ref;
  });
}

function isCfb(bytes: Uint8Array) {
  const magic = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1];
  return bytes.length > 512 && magic.every((b, i) => bytes[i] === b);
}

/** 复合文档目录里的流名是 UTF-16LE */
function includesUtf16(hay: Uint8Array, text: string) {
  const needle = new Uint8Array(text.length * 2);
  for (let i = 0; i < text.length; i++) needle[i * 2] = text.charCodeAt(i);
  outer: for (let i = hay.indexOf(needle[0]); i !== -1 && i <= hay.length - needle.length; i = hay.indexOf(needle[0], i + 1)) {
    for (let j = 1; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
    return true;
  }
  return false;
}
