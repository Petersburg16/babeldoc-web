// 压缩 PDF：无损模式用 qpdf 重新压缩对象流，其余模式用 Ghostscript 重写整个文件（图片降采样并转 JPEG，文字仍可选中）。
// 预设依据对真实论文的实测：Ghostscript /ebook 对论文压缩率最高，但会去掉无障碍标签、拍平表单，
// 少数出版社的 PDF 还会丢书签，所以压缩前后各检查一次结构并提示。压缩后没变小就保留原文件。
import type { PDFDocument } from '@cantoo/pdf-lib';
import {
  GS_BASE,
  attempt,
  eachFile,
  fixUnicodeCMaps,
  fontList,
  gsPdf,
  inspect,
  substitutedCid,
  structureWarnings,
  tryOpen,
  unlockForGs,
  wrongGlyphFonts,
} from '../engines/gs';
import { savePdfLib } from '../engines/pdflib';
import { qpdfOne } from '../engines/qpdf';
import { pdfBlob, readBytes, renamed, type OutputFile, type Report } from '../files';
import { Cancelled, passwordFor } from '../input';

export type CompressLevel = 'lossless' | 'printer' | 'ebook' | 'screen';

const GS_LEVELS: Record<Exclude<CompressLevel, 'lossless'>, string[]> = {
  printer: ['-dPDFSETTINGS=/printer'],
  ebook: ['-dPDFSETTINGS=/ebook'],
  // /screen 默认 72 dpi，扫描件上的字会糊到难以辨认，提到 96 dpi
  screen: ['-dPDFSETTINGS=/screen', '-dColorImageResolution=96', '-dGrayImageResolution=96'],
};

export interface CompressOutcome {
  output: OutputFile;
  before: number;
  after: number;
  /** 结果就是原文件：larger 压缩后没有变小；unsafe 压缩会弄坏内容，原因在 warnings 里 */
  kept: false | 'larger' | 'unsafe';
  warnings: string[];
}

/** 逐个压缩；出错的文件记在 failed 里，其余照常给出结果 */
export function compressFiles(files: File[], level: CompressLevel, report: Report) {
  return eachFile(files, '压缩', report, (file, onProgress) => compressPdf(file, level, onProgress));
}

export async function compressPdf(
  file: File,
  level: CompressLevel,
  onProgress?: (fraction: number) => void,
): Promise<CompressOutcome> {
  const original = await readBytes(file);
  const { output, warnings, unsafe } =
    level === 'lossless'
      ? { output: await lossless(file, original), warnings: [], unsafe: false }
      : await withGhostscript(file, original, level, onProgress);
  if (unsafe || output.length >= file.size) {
    const kept = unsafe ? 'unsafe' : 'larger';
    return { output: { name: file.name, blob: file }, before: file.size, after: file.size, kept, warnings: unsafe ? warnings : [] };
  }
  return {
    output: { name: renamed(file.name, '已压缩'), blob: pdfBlob(output) },
    before: file.size,
    after: output.length,
    kept: false,
    warnings,
  };
}

/** qpdf 重新压缩：内容一点不动，书签、表单、标签结构都在；加密的文件保持原来的加密 */
async function lossless(file: File, bytes: Uint8Array) {
  try {
    const password = await passwordFor(file, bytes);
    return await qpdfOne(['--object-streams=generate', '--recompress-flate', '--compression-level=9'], bytes, password);
  } catch (e) {
    if (e instanceof Cancelled) throw e;
    console.error(e);
    throw new Error('无法读取这个文件：可能已损坏、不是 PDF，或使用了不支持的加密方式');
  }
}

async function withGhostscript(
  file: File,
  original: Uint8Array,
  level: Exclude<CompressLevel, 'lossless'>,
  onProgress?: (fraction: number) => void,
) {
  const { bytes, encrypted } = await unlockForGs(file, original);
  const source = await tryOpen(bytes);
  const before = source ? await attempt(() => inspect(source), null) : null;

  const args = [...GS_BASE, '-dDetectDuplicateImages=true', '-dCompressFonts=true', '-dSubsetFonts=true', ...GS_LEVELS[level]];
  const result = await gsPdf(args, bytes, { onProgress: (page, total) => onProgress?.(page / total) });
  // 字符编号是原字体字形号的未嵌入字体，替代后整篇的字都会画错：宁可不压缩
  const wrong = wrongGlyphFonts(before, result.log);
  if (wrong.length) {
    const warning = `文件里有未嵌入的字体（${fontList(wrong)}），这一档压缩会把这些字替换成错字，所以没有压缩；可以改用「无损」`;
    return { output: original, warnings: [warning], unsafe: true };
  }
  let output = result.output;
  const substituted = substitutedCid(result.log);
  // 替代了中文字体时修正复制出来的文字（只在这种少见情况下多走一遍 pdf-lib，免得白白变大）
  let doc: PDFDocument | null = null;
  if (substituted && output.length < file.size) {
    const fixed = (doc = await tryOpen(output));
    if (fixed) {
      const gsOutput = output;
      output = await attempt(async () => ((await fixUnicodeCMaps(fixed)) ? savePdfLib(fixed) : gsOutput), gsOutput);
    }
  }
  if (output.length >= file.size) return { output, warnings: [], unsafe: false };

  const after = (doc ??= await tryOpen(output));
  const structure = after ? await attempt(() => inspect(after), null) : null;
  return { output, warnings: structureWarnings(before, structure, substituted, encrypted), unsafe: false };
}
