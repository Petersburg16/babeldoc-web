// Office 文件转 PDF：先逐个查看文档要哪些中文字体（批量时取并集，引擎只启动一次），
// 再交给 LibreOffice 逐个转换。单个文件失败不影响其他文件，失败原因随结果一起返回。
// 移植自 BentoPDF（AGPL-3.0）src/js/logic/word-to-pdf.ts、excel-to-pdf-page.ts 等，按本站引擎与界面重写。
import type { InputFormat } from '@matbee/libreoffice-converter/browser';
import { extension, type OutputFile, pdfBlob, readBytes, type Report, stem } from '../files';
import { Cancelled } from '../input';
import { convertOffice, exclusive, OfficeCrash, startOffice } from '../office/engine';
import type { FontKey } from '../office/fonts';
import { inspectOffice } from '../office/sniff';
import { csvToXlsx, txtToUtf8 } from '../office/text';

const FORMATS: Record<string, InputFormat> = {
  doc: 'doc',
  docx: 'docx',
  odt: 'odt',
  rtf: 'rtf',
  txt: 'txt',
  xls: 'xls',
  xlsx: 'xlsx',
  ods: 'ods',
  csv: 'csv',
  ppt: 'ppt',
  pptx: 'pptx',
  odp: 'odp',
};

export interface OfficeFailure {
  name: string;
  reason: string;
}

export interface OfficeResult {
  outputs: OutputFile[];
  failed: OfficeFailure[];
}

/** signal：离开页面时中止，还没开始的文件不再转换，也不再启动引擎 */
export async function officeToPdf(
  files: File[],
  options: { pdfa: boolean },
  report: Report,
  signal?: AbortSignal,
): Promise<OfficeResult> {
  const failed: OfficeFailure[] = [];
  const jobs: { file: File; format: InputFormat }[] = [];
  const keys = new Set<FontKey>();

  report(null, '读取文件');
  for (const file of files) {
    const format = FORMATS[extension(file.name)];
    if (!format) {
      failed.push({ name: file.name, reason: `「${file.name}」不是支持的格式` });
      continue;
    }
    const info = inspectOffice(file.name, await readBytes(file));
    if (info.problem) {
      failed.push({ name: file.name, reason: info.problem });
      continue;
    }
    info.fonts.forEach((k) => keys.add(k));
    jobs.push({ file, format });
  }
  if (!jobs.length) throw new Error(failed.map((f) => f.reason).join('；'));
  if (signal?.aborted) throw new Cancelled();

  const outputs: OutputFile[] = [];
  const used = new Set<string>();
  await exclusive(async () => {
    for (const [i, { file, format }] of jobs.entries()) {
      if (signal?.aborted) throw new Cancelled();
      // 每个文件前都确认一次：引擎崩溃后会在这里重启，字体已够时立即返回
      await startOffice(keys, {
        signal,
        onFonts: (p) => report(p.total ? p.loaded / p.total : null, '准备字体'),
        onStart: () => report(null, '启动 LibreOffice'),
      });
      const label = jobs.length > 1 ? `转换「${file.name}」（${i + 1}/${jobs.length}）` : `转换「${file.name}」`;
      report(i / jobs.length, label);
      try {
        let input: Uint8Array = await readBytes(file);
        let as = format;
        if (format === 'csv') [input, as] = [csvToXlsx(input, stem(file.name)), 'xlsx'];
        else if (format === 'txt') input = txtToUtf8(input);
        const data = await convertOffice(input, as, options, (f) => report((i + f) / jobs.length, label));
        outputs.push({ name: outputName(file.name, used), blob: pdfBlob(data) });
      } catch (e) {
        if (e instanceof Cancelled) throw e;
        console.error(e);
        failed.push({ name: file.name, reason: friendlyError(file.name, e) });
      }
    }
  });
  if (!outputs.length) throw new Error(failed.map((f) => f.reason).join('；'));
  return { outputs, failed };
}

/** 「报告.docx」→「报告.pdf」；同名的（报告.docx、报告.xlsx）带上原扩展名区分 */
function outputName(name: string, used: Set<string>) {
  let out = `${stem(name)}.pdf`;
  if (used.has(out)) out = `${stem(name)}-${extension(name)}.pdf`;
  for (let i = 2; used.has(out); i++) out = `${stem(name)}-${i}.pdf`;
  used.add(out);
  return out;
}

function friendlyError(name: string, e: unknown) {
  const message = e instanceof Error ? e.message : String(e);
  if (e instanceof OfficeCrash || /memory|RangeError|Aborted\(/i.test(message)) {
    return `转换「${name}」时引擎崩溃，可能是内存不足。请关闭其他标签页，或一次少转几个文件后重试`;
  }
  if (/type detection|Unsupported URL|Failed to load document/i.test(message)) {
    return `无法打开「${name}」：文件可能已损坏、设置了打开密码，或不是支持的格式`;
  }
  if (/empty output/i.test(message)) return `「${name}」转换后没有内容，可能是空文档`;
  return `「${name}」转换失败：${message}`;
}
