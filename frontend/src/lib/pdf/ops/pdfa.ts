// 转为 PDF/A：Ghostscript pdfwrite 的 PDF/A 模式，嵌入全部字体、统一为 sRGB 并写入 OutputIntent 与 XMP。
// 移植自 BentoPDF（AGPL-3.0）src/js/logic/pdf-to-pdfa-page.ts 与 src/js/utils/ghostscript-loader.ts，按本站引擎与界面重写。
// 参数按 veraPDF 实测修正：OutputIntent 的 /S 一律是 /GTS_PDFA1（BentoPDF 给 2b/3b 写成 /GTS_PDFA，验证不过），
// 色彩转换用 RGB（UseDeviceIndependentColor 会让 Ghostscript 放弃 PDF/A），ICC 用 Ghostscript 内置的 sRGB，开 -dSAFER。
import {
  GS_BASE,
  GsError,
  LOSSLESS_IMAGES,
  attempt,
  eachFile,
  fixGbkFontNames,
  fixUnicodeCMaps,
  fontList,
  gsPdf,
  hasEmbeddedFiles,
  inspect,
  markPrintable,
  saveDoc,
  stripAttachments,
  structureWarnings,
  substitutedCid,
  tryOpen,
  unlockForGs,
  wrongGlyphFonts,
} from '../engines/gs';
import { pdfBlob, readBytes, renamed, type OutputFile, type Report } from '../files';

export type PdfALevel = '2b' | '3b' | '1b';

const ICC = '%rom%iccprofiles/srgb.icc';

const PDFA_DEF = [
  '%!',
  '[/_objdef {icc_PDFA} /type /stream /OBJ pdfmark',
  '[{icc_PDFA} << /N 3 >> /PUT pdfmark',
  `[{icc_PDFA} (${ICC}) (r) file /PUT pdfmark`,
  '[/_objdef {OutputIntent_PDFA} /type /dict /OBJ pdfmark',
  '[{OutputIntent_PDFA} << /Type /OutputIntent /S /GTS_PDFA1 /DestOutputProfile {icc_PDFA}',
  '  /OutputConditionIdentifier (sRGB IEC61966-2.1) /Info (sRGB IEC61966-2.1) /RegistryName (http://www.color.org) >> /PUT pdfmark',
  '[{Catalog} << /OutputIntents [ {OutputIntent_PDFA} ] >> /PUT pdfmark',
  '',
].join('\n');

export interface PdfAOutcome {
  output: OutputFile;
  warnings: string[];
}

/** 逐个转换；出错的文件记在 failed 里，其余照常给出结果 */
export function convertFiles(files: File[], level: PdfALevel, keepImages: boolean, report: Report) {
  return eachFile(files, '转换', report, (file, onProgress) => convertToPdfA(file, level, keepImages, onProgress));
}

export async function convertToPdfA(
  file: File,
  level: PdfALevel,
  keepImages: boolean,
  onProgress?: (fraction: number) => void,
): Promise<PdfAOutcome> {
  const part = level.charAt(0);
  const { bytes, encrypted } = await unlockForGs(file, await readBytes(file));

  // 补上批注的打印标志，否则链接会被 Ghostscript 当作不合规批注整个去掉；附件也在这里去掉
  const source = await tryOpen(bytes);
  const before = source ? await attempt(() => inspect(source), null) : null;
  const prepared = source
    ? await attempt(
        async () => {
          const marked = await markPrintable(source);
          const attachments = await stripAttachments(source);
          return { input: marked || attachments ? await saveDoc(source, false) : bytes, attachments };
        },
        { input: bytes, attachments: 0 },
      )
    : { input: bytes, attachments: 0 };
  const input = prepared.input;

  const args = [
    ...GS_BASE,
    '--permit-file-read=/tmp/',
    `-dPDFA=${part}`,
    '-dPDFACompatibilityPolicy=1',
    `-dCompatibilityLevel=${part === '1' ? '1.4' : '1.7'}`,
    '-sColorConversionStrategy=RGB',
    '-sProcessColorModel=DeviceRGB',
    `-sOutputICCProfile=${ICC}`,
    '-dEmbedAllFonts=true',
    '-dSubsetFonts=true',
    // 默认会把图片重新有损压缩（实测 41 MB 的论文变成 6 MB），归档时通常要原画质
    ...(keepImages ? LOSSLESS_IMAGES : []),
  ];
  const result = await gsPdf(args, input, {
    before: { '/tmp/pdfa.ps': PDFA_DEF },
    onProgress: (page, total) => onProgress?.(page / total),
  });

  // 字符编号是原字体字形号的未嵌入字体，替代后整篇的字都会画错，veraPDF 却照样判合格
  const wrong = wrongGlyphFonts(before, result.log);
  if (wrong.length) {
    throw new GsError(
      `文件里有未嵌入的字体（${fontList(wrong)}），替代后会显示成错字，无法生成可靠的 PDF/A：请在原软件导出 PDF 时勾选“嵌入字体”后再转换`,
    );
  }

  // 遇到做不到的情况，Ghostscript 会退回普通 PDF 而且照样返回成功，不能把它当成 PDF/A 交出去
  const reverted = result.log.find((l) => /reverting to normal/i.test(l));
  if (reverted) {
    console.error('Ghostscript 放弃了 PDF/A', result.log);
    const reason = /CID 0/.test(reverted)
      ? '文件里有字体用到了 PDF/A 不允许的 0 号字形（CID 0），常见于表单或未嵌入中文字体的文件'
      : reverted.replace(/^.*?Ghostscript [\d.]+:\s*/, '').replace(/,?\s*reverting to normal.*$/i, '');
    throw new GsError(`无法生成合规的 PDF/A：${reason}。可以先在 PDF 阅读器里“打印”为新的 PDF，再转换`);
  }

  const gsOutput = result.output;
  const substituted = substitutedCid(result.log);
  const doc = await tryOpen(gsOutput);
  // 修正知网等文件的 GBK 字体名（PDF/A-2/3 规则 6.1.8），以及替代中文字体后复制出来的乱码
  const output = !doc
    ? gsOutput
    : await attempt(async () => {
        const names = part === '1' ? 0 : await fixGbkFontNames(doc);
        const cmaps = substituted ? await fixUnicodeCMaps(doc) : 0;
        // PDF/A-1 不允许对象流；2、3 允许，Ghostscript 自己也用，保留可避免体积变大
        return names || cmaps ? saveDoc(doc, part !== '1') : gsOutput;
      }, gsOutput);
  const after = doc ? await attempt(() => inspect(doc), null) : null;
  // 预处理没能去掉的附件（pdf-lib 读不了原文件等）会让结果验证不过
  if (doc && (await attempt(() => hasEmbeddedFiles(doc), false))) {
    throw new GsError('无法生成合规的 PDF/A：文件里的附件没能去除。可以先在 PDF 阅读器里“打印”为新的 PDF，再转换');
  }

  const warnings = structureWarnings(before, after, substituted, false);
  if (encrypted) warnings.unshift('PDF/A 不允许加密，原文件的密码和权限限制已去除');
  if (prepared.attachments) warnings.push(`附件无法随 PDF/A 保留，已去除 ${prepared.attachments} 个附件`);
  if (result.log.some((l) => l.includes('annotation will not be present'))) {
    warnings.push('部分批注不符合 PDF/A 要求，已去除');
  }
  // 带 ICC 色彩的 JPEG 要转成 sRGB，没法原样透传，只能无损重存，照片多的文件会变大
  if (keepImages && output.length > file.size * 2 && output.length > 2_000_000) {
    warnings.push(`文件变大到原来的 ${(output.length / file.size).toFixed(1)} 倍，主要是图片改为无损保存；想要小一些可以关闭“保留图片原画质”`);
  }
  return { output: { name: renamed(file.name, 'PDFA'), blob: pdfBlob(output) }, warnings };
}
