// 文档属性：读取、修改、清除 Info 字典与 XMP 元数据。
// 参考 BentoPDF（AGPL-3.0）src/js/logic/{view,edit,remove}-metadata-page.ts，按本站引擎与界面重写：
// 打开时不让 pdf-lib 改写 Producer 和修改时间；中文自定义字段名按 UTF-8、值按 UTF-16 写入（BentoPDF 写出来是乱码）；
// 修改或清除后回收不再被引用的对象，否则旧的作者、XMP 原文仍以孤立对象留在文件里。
import {
  PDFArray,
  PDFDict,
  type PDFDocument,
  PDFHexString,
  PDFName,
  type PDFObject,
  PDFRawStream,
  PDFRef,
  PDFStream,
  PDFString,
  decodePDFRawStream,
} from '@cantoo/pdf-lib';
import { openPdfLib } from '../engines/pdflib';

export const TEXT_FIELDS = ['title', 'author', 'subject', 'keywords', 'creator', 'producer'] as const;
export type TextField = (typeof TEXT_FIELDS)[number];

const INFO_KEY: Record<TextField | 'creationDate' | 'modDate', string> = {
  title: 'Title',
  author: 'Author',
  subject: 'Subject',
  keywords: 'Keywords',
  creator: 'Creator',
  producer: 'Producer',
  creationDate: 'CreationDate',
  modDate: 'ModDate',
};
const STANDARD = new Set([...Object.values(INFO_KEY), 'Trapped']);
/** pdf-lib 能按 Info 重写 XMP 的 PDF/A 级别 */
const SYNCABLE = ['1B', '2B', '2U', '3B', '3U'] as const;

export type XmpField = TextField | 'creationDate' | 'modDate';

export interface PdfMetadata {
  version: string;
  pageCount: number;
  text: Record<TextField, string>;
  creationDate: Date | null;
  modDate: Date | null;
  custom: [string, string][];
  hasXmp: boolean;
  /** 例如 “PDF/A-2B”；不是 PDF/A 时为空 */
  pdfa: string;
  /** 例如 “PDF/UA-1”；不是 PDF/UA 时为空 */
  pdfua: string;
  /** Info 里没有、取自 XMP 的属性 */
  fromXmp: XmpField[];
}

export interface MetadataEdit {
  /** 只放改过的字段；空字符串表示删除该项 */
  text: Partial<Record<TextField, string>>;
  creationDate?: Date | null;
  modDate?: Date | null;
  /** 传入时整体替换全部自定义字段 */
  custom?: [string, string][];
}

const N = (s: string) => PDFName.of(s);

function infoDict(doc: PDFDocument) {
  const info = doc.context.lookup(doc.context.trailerInfo.Info);
  return info instanceof PDFDict ? info : undefined;
}

function decodeString(v: PDFString | PDFHexString) {
  const bytes = v.asBytes();
  // PDF 2.0 允许 UTF-8（带 BOM），pdf-lib 只认 UTF-16 和 PDFDocEncoding
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) return new TextDecoder().decode(bytes.subarray(3));
  return v.decodeText();
}

// 中文名称按 UTF-8 字节写成 #E9#A1… 才合规；PDFName.of 直接收中文会写出无效的名称
const utf8Name = (s: string) => N(String.fromCharCode(...new TextEncoder().encode(s)));

function nameText(n: PDFName) {
  const raw = n.decodeText();
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(raw, (c) => c.charCodeAt(0)));
  } catch {
    return raw;
  }
}

function textOf(v: PDFObject | undefined): string {
  if (v instanceof PDFString || v instanceof PDFHexString) return decodeString(v).replace(/\0/g, '');
  if (v instanceof PDFName) return nameText(v);
  return v === undefined ? '' : v.toString();
}

function dateOf(v: PDFObject | undefined): Date | null {
  if (!(v instanceof PDFString || v instanceof PDFHexString)) return null;
  try {
    const d = v.decodeDate();
    return Number.isNaN(d.getTime()) ? null : d;
  } catch {
    return null; // 格式不对的日期字符串很常见，当作没有
  }
}

function readXmp(doc: PDFDocument) {
  try {
    const s = doc.catalog.lookup(N('Metadata'));
    if (!(s instanceof PDFRawStream)) return undefined;
    return new TextDecoder('utf-8').decode(decodePDFRawStream(s).decode());
  } catch {
    return undefined;
  }
}

function pdfaOf(xmp: string | undefined) {
  if (!xmp) return null;
  const part = /<pdfaid:part>\s*(\d)\s*</.exec(xmp) ?? /pdfaid:part\s*=\s*["'](\d)["']/.exec(xmp);
  if (!part) return null;
  const level = /<pdfaid:conformance>\s*([A-Za-z])\s*</.exec(xmp) ?? /pdfaid:conformance\s*=\s*["']([A-Za-z])["']/.exec(xmp);
  return { part: Number(part[1]), level: level?.[1].toUpperCase() ?? '' };
}

/** PDF/UA 只靠 XMP 里的 pdfuaid 声明身份（第 2 部分还要带修订年份） */
function pdfuaOf(xmp: string | undefined) {
  if (!xmp) return null;
  const part = /<pdfuaid:part>\s*(\d)\s*</.exec(xmp) ?? /pdfuaid:part\s*=\s*["'](\d)["']/.exec(xmp);
  if (!part) return null;
  const rev = /<pdfuaid:rev>\s*(\d+)\s*</.exec(xmp) ?? /pdfuaid:rev\s*=\s*["'](\d+)["']/.exec(xmp);
  return { part: Number(part[1]), rev: rev?.[1] ?? '' };
}

/** 文件声明遵循、且要求字体全部嵌入的标准（PDF/A、PDF/UA、PDF/X），例如 “PDF/A-2B”；都不是时为空 */
export function embedStandard(doc: PDFDocument) {
  const xmp = readXmp(doc);
  const a = pdfaOf(xmp);
  if (a) return `PDF/A-${a.part}${a.level}`;
  const ua = pdfuaOf(xmp);
  if (ua) return `PDF/UA-${ua.part}`;
  if (/pdfxid:GTS_PDFXVersion/.test(xmp ?? '') || infoDict(doc)?.has(N('GTS_PDFXVersion'))) return 'PDF/X';
  return '';
}

const NS = {
  rdf: 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
  xml: 'http://www.w3.org/XML/1998/namespace',
  dc: 'http://purl.org/dc/elements/1.1/',
  xmp: 'http://ns.adobe.com/xap/1.0/',
  pdf: 'http://ns.adobe.com/pdf/1.3/',
  pdfuaid: 'http://www.aiim.org/pdfua/ns/id/',
};

type XmpValues = Partial<Record<TextField, string>> & { creationDate?: Date; modDate?: Date };

/** XMP 里与 Info 对应的属性（PDF 2.0 的文件可以只写在 XMP 里）；XMP 写坏了就当没有 */
function xmpValues(xml: string | undefined): XmpValues {
  const start = xml?.indexOf('<') ?? -1;
  if (!xml || start < 0 || typeof DOMParser === 'undefined') return {};
  let dom: Document;
  try {
    dom = new DOMParser().parseFromString(xml.slice(start, xml.lastIndexOf('>') + 1), 'application/xml');
  } catch {
    return {};
  }
  if (dom.getElementsByTagName('parsererror').length) return {};
  const descriptions = [...dom.getElementsByTagNameNS(NS.rdf, 'Description')];
  const prop = (ns: string, name: string, sep = '; ') => {
    const el = dom.getElementsByTagNameNS(ns, name)[0];
    if (!el) {
      // 简写形式：<rdf:Description pdf:Producer="…">
      const d = descriptions.find((x) => x.hasAttributeNS(ns, name));
      return d?.getAttributeNS(ns, name)?.trim() ?? '';
    }
    const items = [...el.getElementsByTagNameNS(NS.rdf, 'li')];
    if (!items.length) return el.textContent?.trim() ?? '';
    // 多语言（Alt）取默认语言；列表（Seq、Bag）全部连起来
    if (el.getElementsByTagNameNS(NS.rdf, 'Alt').length) {
      const li = items.find((x) => x.getAttributeNS(NS.xml, 'lang') === 'x-default') ?? items[0];
      return li.textContent?.trim() ?? '';
    }
    return items
      .map((x) => x.textContent?.trim() ?? '')
      .filter(Boolean)
      .join(sep);
  };
  const date = (name: string) => {
    const s = prop(NS.xmp, name);
    const d = s ? new Date(s) : null;
    return d && !Number.isNaN(d.getTime()) ? d : undefined;
  };
  return {
    title: prop(NS.dc, 'title'),
    author: prop(NS.dc, 'creator'),
    subject: prop(NS.dc, 'description'),
    keywords: prop(NS.pdf, 'Keywords') || prop(NS.dc, 'subject', ', '),
    creator: prop(NS.xmp, 'CreatorTool'),
    producer: prop(NS.pdf, 'Producer'),
    creationDate: date('CreateDate'),
    modDate: date('ModifyDate'),
  };
}

const esc = (s: string) => s.replace(/[<>&"']/g, (c) => `&#${c.charCodeAt(0)};`);
const xmpDate = (d: Date) => `${d.toISOString().split('.')[0]}Z`;

/** 按 Info 的当前值写一份最小的 XMP 并带上 PDF/UA 标识（PDF/UA 要求 XMP 里有标题） */
function writeUaXmp(doc: PDFDocument, info: PDFDict, ua: { part: number; rev: string }) {
  const get = (key: string) => doc.context.lookup(info.get(N(key)));
  const t = (key: string) => esc(textOf(get(key)).trim());
  const alt = (tag: string, v: string) => `<${tag}><rdf:Alt><rdf:li xml:lang="x-default">${v}</rdf:li></rdf:Alt></${tag}>`;
  const props = ['<dc:format>application/pdf</dc:format>'];
  if (t('Title')) props.push(alt('dc:title', t('Title')));
  if (t('Author')) props.push(`<dc:creator><rdf:Seq><rdf:li>${t('Author')}</rdf:li></rdf:Seq></dc:creator>`);
  if (t('Subject')) props.push(alt('dc:description', t('Subject')));
  if (t('Keywords')) props.push(`<pdf:Keywords>${t('Keywords')}</pdf:Keywords>`);
  if (t('Creator')) props.push(`<xmp:CreatorTool>${t('Creator')}</xmp:CreatorTool>`);
  if (t('Producer')) props.push(`<pdf:Producer>${t('Producer')}</pdf:Producer>`);
  const created = dateOf(get('CreationDate'));
  const modified = dateOf(get('ModDate'));
  if (created) props.push(`<xmp:CreateDate>${xmpDate(created)}</xmp:CreateDate>`);
  if (modified) props.push(`<xmp:ModifyDate>${xmpDate(modified)}</xmp:ModifyDate>`);
  props.push(`<pdfuaid:part>${ua.part}</pdfuaid:part>`);
  if (ua.rev) props.push(`<pdfuaid:rev>${ua.rev}</pdfuaid:rev>`);
  const xml = [
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>',
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">',
    `<rdf:RDF xmlns:rdf="${NS.rdf}">`,
    `<rdf:Description rdf:about="" xmlns:dc="${NS.dc}" xmlns:xmp="${NS.xmp}" xmlns:pdf="${NS.pdf}" xmlns:pdfuaid="${NS.pdfuaid}">`,
    ...props,
    '</rdf:Description>',
    '</rdf:RDF>',
    '</x:xmpmeta>',
    '<?xpacket end="w"?>',
  ].join('\n');
  // 不压缩，便于校验工具直接读取
  const stream = doc.context.stream(new TextEncoder().encode(xml), { Type: 'Metadata', Subtype: 'XML' });
  doc.catalog.set(N('Metadata'), doc.context.register(stream));
}

/** 保存并压缩；PDF/A-1 不允许对象流（pdf-lib 会直接报错），这时不用 */
export function saveDoc(doc: PDFDocument) {
  return doc.save({ useObjectStreams: pdfaOf(readXmp(doc))?.part !== 1 });
}

/** bytes 需未加密（先用 unlockPdf 解开） */
export async function readMetadata(bytes: Uint8Array): Promise<PdfMetadata> {
  const doc = await openPdfLib(bytes);
  const info = infoDict(doc);
  const get = (key: string) => (info ? doc.context.lookup(info.get(N(key))) : undefined);
  const xmp = readXmp(doc);
  const fallback = xmpValues(xmp);
  const fromXmp: XmpField[] = [];
  const text = {} as Record<TextField, string>;
  for (const f of TEXT_FIELDS) {
    text[f] = textOf(get(INFO_KEY[f])).trim();
    if (!text[f] && fallback[f]) {
      text[f] = fallback[f];
      fromXmp.push(f);
    }
  }
  const dates = { creationDate: dateOf(get('CreationDate')), modDate: dateOf(get('ModDate')) };
  for (const f of ['creationDate', 'modDate'] as const) {
    if (!dates[f] && fallback[f]) {
      dates[f] = fallback[f];
      fromXmp.push(f);
    }
  }
  const custom: [string, string][] = [];
  for (const [k, v] of info?.entries() ?? []) {
    const key = nameText(k);
    if (!STANDARD.has(key)) custom.push([key, textOf(doc.context.lookup(v))]);
  }
  const pdfa = pdfaOf(xmp);
  const pdfua = pdfuaOf(xmp);
  return {
    version: doc.context.header.getVersionString(),
    pageCount: doc.getPageCount(),
    text,
    ...dates,
    custom,
    hasXmp: doc.catalog.has(N('Metadata')),
    pdfa: pdfa ? `PDF/A-${pdfa.part}${pdfa.level}` : '',
    pdfua: pdfua ? `PDF/UA-${pdfua.part}` : '',
    fromXmp,
  };
}

/**
 * 修改属性。XMP 里存着旧值且阅读器优先读它：普通文件直接删掉；PDF/A 按新值重写（删了就不再合规）；
 * PDF/UA 按新值写一份带 PDF/UA 标识的。
 */
export async function editMetadata(bytes: Uint8Array, edit: MetadataEdit) {
  const doc = await openPdfLib(bytes);
  // getInfoDict 在类型里是私有的；没有 Info 字典时会新建一个
  const info = (doc as unknown as { getInfoDict(): PDFDict }).getInfoDict();
  const get = (key: string) => doc.context.lookup(info.get(N(key)));
  const put = (key: string, value: PDFObject | null) => (value ? info.set(N(key), value) : info.delete(N(key)));

  const xmp = readXmp(doc);
  const pdfa = pdfaOf(xmp);
  const level = pdfa ? `${pdfa.part}${pdfa.level}` : '';
  const syncable = (SYNCABLE as readonly string[]).includes(level);
  // XMP 要删掉或按 Info 重写时，先把只写在 XMP 里的属性（表单里标着“取自 XMP”）搬进 Info，否则会丢；
  // 原样保留 XMP 的 PDF/A 不搬，搬过去的值与 XMP 稍有出入（例如多位作者）就不再合规
  if (!pdfa || syncable) {
    const fallback = xmpValues(xmp);
    for (const f of TEXT_FIELDS) {
      const v = fallback[f];
      if (v && edit.text[f] === undefined && !textOf(get(INFO_KEY[f])).trim()) put(INFO_KEY[f], PDFHexString.fromText(v));
    }
    for (const f of ['creationDate', 'modDate'] as const) {
      const v = fallback[f];
      if (v && edit[f] === undefined && !dateOf(get(INFO_KEY[f]))) put(INFO_KEY[f], PDFString.fromDate(v));
    }
  }

  for (const f of TEXT_FIELDS) {
    const value = edit.text[f];
    if (value !== undefined) put(INFO_KEY[f], value.trim() ? PDFHexString.fromText(value.trim()) : null);
  }
  if (edit.creationDate !== undefined) put('CreationDate', edit.creationDate && PDFString.fromDate(edit.creationDate));
  if (edit.modDate !== undefined) put('ModDate', edit.modDate && PDFString.fromDate(edit.modDate));
  if (edit.custom) {
    const wanted = new Map<string, string>();
    for (const [k, v] of edit.custom) if (k.trim()) wanted.set(k.trim(), v.trim());
    for (const [k, v] of info.entries()) {
      const key = nameText(k);
      if (STANDARD.has(key)) continue;
      // 没改过的字段原样保留：数字、布尔、名称这类值改写成文字会变类型
      if (wanted.get(key) === textOf(doc.context.lookup(v)).trim()) wanted.delete(key);
      else info.delete(k);
    }
    for (const [k, v] of wanted) if (v) info.set(utf8Name(k), PDFHexString.fromText(v));
  }

  const ua = pdfuaOf(xmp);
  if (syncable) {
    // 对已是 PDF/A 的文件，convertToPDFA 不动输出意图，只打开“保存时按 Info 重写 XMP”，并保留其他扩展信息
    doc.convertToPDFA({ conformance: level as (typeof SYNCABLE)[number] });
  } else if (!pdfa && ua) {
    writeUaXmp(doc, info, ua);
  } else if (!pdfa) {
    doc.catalog.delete(N('Metadata'));
  }
  // 其余 PDF/A 级别（A 级、第 4 部分）pdf-lib 不会重写，保留原 XMP
  collectGarbage(doc);
  return saveDoc(doc);
}

/** 清除全部元数据：Info 字典、文档级与对象级 XMP、PieceInfo、文档 ID，并回收孤立对象 */
export async function clearMetadata(bytes: Uint8Array) {
  const doc = await openPdfLib(bytes);
  const ctx = doc.context;
  ctx.trailerInfo.Info = undefined;
  ctx.trailerInfo.ID = undefined;
  doc.catalog.delete(N('Metadata'));
  doc.catalog.delete(N('PieceInfo'));
  doc.catalog.delete(N('Info')); // 不合规范，但 MuPDF 生成的文件会在目录里再放一份生成程序
  for (const [, obj] of ctx.enumerateIndirectObjects()) {
    const d = obj instanceof PDFDict ? obj : obj instanceof PDFStream ? obj.dict : undefined;
    d?.delete(N('Metadata'));
    d?.delete(N('PieceInfo'));
  }
  const removed = collectGarbage(doc);
  return { bytes: await saveDoc(doc), removed };
}

// pdf-lib 会把读入的每个对象原样写回，删掉字典里的引用并不会让数据从文件里消失，所以从 trailer 出发标记可达对象，其余删除。
function collectGarbage(doc: PDFDocument) {
  const ctx = doc.context;
  const seen = new Set<string>();
  const stack: (PDFObject | undefined)[] = [ctx.trailerInfo.Root, ctx.trailerInfo.Info, ctx.trailerInfo.Encrypt];
  while (stack.length) {
    const o = stack.pop();
    if (o instanceof PDFRef) {
      if (seen.has(o.tag)) continue;
      seen.add(o.tag);
      stack.push(ctx.lookup(o));
    } else if (o instanceof PDFDict) {
      for (const [, v] of o.entries()) stack.push(v);
    } else if (o instanceof PDFArray) {
      stack.push(...o.asArray());
    } else if (o instanceof PDFStream) {
      stack.push(o.dict);
    }
  }
  let removed = 0;
  for (const [ref] of ctx.enumerateIndirectObjects()) {
    if (!seen.has(ref.tag)) {
      ctx.delete(ref);
      removed++;
    }
  }
  return removed;
}
