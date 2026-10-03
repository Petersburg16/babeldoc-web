// 纯文本输入的预处理。
// CSV：这个 LibreOffice 构建给 CSV 加的导入参数是错位的（实测从第 76 行开始读，表格全空），
// 不带参数时又不认引号，所以自己解析 CSV，生成一个最小的 xlsx 再交给 LibreOffice。
// 中文 Windows 上 Excel 另存的 CSV、记事本存的 TXT 常是 GBK 编码，统一先转成 UTF-8。
// Excel 另存“Unicode 文本”、记事本选 Unicode 存的是带 BOM 的 UTF-16，要先认出来，不然会当成 GBK 解成乱码。
import { strToU8, zipSync } from 'fflate';

export type TextEncoding = 'utf-8' | 'utf-16le' | 'utf-16be' | 'gb18030';

/** 有 UTF-16 BOM 按 UTF-16 解码；否则先试 UTF-8，解不开时按 GB18030（GBK 的超集）解码 */
export function decodeText(bytes: Uint8Array): { text: string; encoding: TextEncoding } {
  const bom = bytes[0] === 0xff && bytes[1] === 0xfe ? 'utf-16le' : bytes[0] === 0xfe && bytes[1] === 0xff ? 'utf-16be' : '';
  if (bom) return { text: new TextDecoder(bom).decode(bytes), encoding: bom };
  try {
    return { text: new TextDecoder('utf-8', { fatal: true }).decode(bytes), encoding: 'utf-8' };
  } catch {
    return { text: new TextDecoder('gb18030').decode(bytes), encoding: 'gb18030' };
  }
}

/** TXT：GBK 的转成带 BOM 的 UTF-8；UTF-8 和带 BOM 的 UTF-16 LibreOffice 自己认得，原样交给它 */
export function txtToUtf8(bytes: Uint8Array) {
  const { text, encoding } = decodeText(bytes);
  if (encoding !== 'gb18030') return bytes;
  const body = new TextEncoder().encode(text);
  const out = new Uint8Array(body.length + 3);
  out.set([0xef, 0xbb, 0xbf]);
  out.set(body, 3);
  return out;
}

/** CSV → xlsx（只有一个工作表，长文字自动换行；看起来像数字的写成数字，0034 这类编号保留原样） */
export function csvToXlsx(bytes: Uint8Array, sheetName: string) {
  const { text } = decodeText(bytes);
  const rows = parseCsv(text, guessDelimiter(text));
  const widths: number[] = [];
  const body = rows
    .map((row, r) => {
      const cells = row
        .map((value, c) => {
          widths[c] = Math.max(widths[c] ?? 0, displayWidth(value));
          if (value === '') return '';
          const ref = `${column(c)}${r + 1}`;
          if (NUMBER.test(value)) return `<c r="${ref}" s="1"><v>${value}</v></c>`;
          return `<c r="${ref}" t="inlineStr" s="1"><is><t xml:space="preserve">${escapeXml(value)}</t></is></c>`;
        })
        .join('');
      return `<row r="${r + 1}">${cells}</row>`;
    })
    .join('');
  const cols = widths
    .map((w, c) => `<col min="${c + 1}" max="${c + 1}" width="${Math.min(50, Math.max(8, w + 2))}" customWidth="1"/>`)
    .join('');
  const name = escapeXml(sheetName.replace(/[\\/?*[\]:]/g, '_').slice(0, 31) || 'Sheet1');
  const ns = 'http://schemas.openxmlformats.org/';
  return zipSync({
    '[Content_Types].xml': xml(
      `<Types xmlns="${ns}package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>`,
    ),
    '_rels/.rels': xml(
      `<Relationships xmlns="${ns}package/2006/relationships"><Relationship Id="rId1" Type="${ns}officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
    ),
    'xl/workbook.xml': xml(
      `<workbook xmlns="${ns}spreadsheetml/2006/main" xmlns:r="${ns}officeDocument/2006/relationships"><sheets><sheet name="${name}" sheetId="1" r:id="rId1"/></sheets></workbook>`,
    ),
    'xl/_rels/workbook.xml.rels': xml(
      `<Relationships xmlns="${ns}package/2006/relationships"><Relationship Id="rId1" Type="${ns}officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="${ns}officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`,
    ),
    'xl/styles.xml': xml(
      `<styleSheet xmlns="${ns}spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs></styleSheet>`,
    ),
    'xl/worksheets/sheet1.xml': xml(
      `<worksheet xmlns="${ns}spreadsheetml/2006/main">${cols ? `<cols>${cols}</cols>` : ''}<sheetData>${body}</sheetData></worksheet>`,
    ),
  });
}

// 不以 0 开头的整数或小数，最多 15 位有效数字（再长的多半是身份证号、订单号，按文字保留）
const NUMBER = /^-?(?:0|[1-9]\d{0,14})(?:\.\d{1,15})?$/;

function parseCsv(text: string, delimiter: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch !== '"') field += ch;
      else if (text[i + 1] === '"') {
        field += '"';
        i++;
      } else quoted = false;
    } else if (ch === '"' && field === '') quoted = true;
    else if (ch === delimiter) {
      row.push(field);
      field = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else field += ch;
  }
  if (field !== '' || row.length) rows.push([...row, field]);
  return rows;
}

/** 前几行里出现最多的分隔符（欧洲地区常用分号，也有人把制表符分隔的文件存成 .csv） */
function guessDelimiter(text: string) {
  const head = text.slice(0, 4096);
  let best = ',';
  let most = 0;
  for (const d of [',', ';', '\t', '|']) {
    const n = head.split(d).length - 1;
    if (n > most) [best, most] = [d, n];
  }
  return best;
}

/** 列宽按字符算，中文算两个 */
function displayWidth(value: string) {
  let w = 0;
  for (const line of value.split('\n')) {
    let lw = 0;
    for (const ch of line) lw += ch.codePointAt(0)! > 0x2e80 ? 2 : 1;
    w = Math.max(w, lw);
  }
  return w;
}

function column(index: number) {
  let s = '';
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
}

function escapeXml(s: string) {
  // XML 1.0 不允许的控制字符直接去掉
  return s
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function xml(body: string) {
  return strToU8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n${body}`);
}
