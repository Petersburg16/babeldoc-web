// PDF 转 Word：pdf2docx（GPL-3.0）+ PyMuPDF 在 Pyodide 里分析版面、生成 .docx，逐页报告进度。
// 移植自 BentoPDF（AGPL-3.0）src/js/logic/pdf-to-docx-page.ts 与 @bentopdf/pymupdf-wasm 的 pdfToDocx，按本站引擎与界面重写：
// 去掉 Ghostscript 转 RGB 的预处理（BentoPDF 线上那一步本来就 404），保留它把 Indexed/JPX、CMYK 图片转 RGB 的补丁，
// 否则一张这样的图片就会让整份文档转换失败。输入须已解密（调用方先 unlockPdf）。
import { engineWheels, type PyProgress, runPymupdf } from '../engines/pymupdf';

export const DOCX_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

export interface DocxMeta {
  /** 要转换的页数 */
  pages: number;
  /** 转换失败、已跳过的页（1 起计） */
  failed: number[];
  /** 是否有文字层；没有时多半是扫描件，Word 里只有图片 */
  text: boolean;
}

/** 进度阶段：extract 逐页提取版面、parse 逐页识别段落表格、write 逐页写入 Word；done/total 为页数 */
export type DocxStage = 'init' | 'extract' | 'parse' | 'write';

const PY = `
import json, logging
import pymupdf
from docx import Document
from pdf2docx import Converter
from pdf2docx.image.ImagesExtractor import ImagesExtractor
from pdf2docx.page.RawPageFactory import RawPageFactory

# pdf2docx 默认按 INFO 逐行打日志，进度改由 progress 回调报告
logging.disable(logging.WARNING)


class UserError(Exception):
    pass


_orig_to_raw_dict = ImagesExtractor._to_raw_dict


def _rgb_to_raw_dict(image, bbox):
    # PNG 只能存灰度或 RGB：CMYK、Indexed、ICC 等色彩空间先转 RGB（沿用 BentoPDF 按色彩空间名判断的写法）
    pix = image
    needs = False
    if getattr(pix, 'colorspace', None):
        name = (pix.colorspace.name or '').upper()
        if 'CMYK' in name or name not in ('DEVICEGRAY', 'GRAY', 'DEVICERGB', 'RGB', 'SRGB', ''):
            needs = True
    if not needs and hasattr(pix, 'n') and hasattr(pix, 'alpha'):
        if (pix.n == 4 and not pix.alpha) or pix.n > 4:
            needs = True
    if needs:
        try:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        except Exception:
            pass
    return _orig_to_raw_dict(pix, bbox)


ImagesExtractor._to_raw_dict = staticmethod(_rgb_to_raw_dict)

# 版面提取在 Converter.parse_document 里逐页进行，没有回调：包一层页面工厂来计数
_tick = None
_orig_create = RawPageFactory.create.__func__


def _counting_create(cls, page_engine, backend='pymupdf'):
    if _tick:
        _tick()
    return _orig_create(cls, page_engine, backend)


RawPageFactory.create = classmethod(_counting_create)


def bdw_to_docx(src, dst, opts_json, progress):
    global _tick
    opts = json.loads(opts_json)
    cv = Converter(src)
    try:
        if cv.fitz_doc.needs_pass:
            raise UserError('这个 PDF 仍有打开密码，请先解除密码')
        settings = cv.default_settings
        # 多进程在 Pyodide 里不可用
        settings.update(ignore_page_error=True, multi_processing=False)
        cv.load_pages(pages=opts.get('pages') or None)
        targets = [p for p in cv.pages if not p.skip_parsing]
        n = len(targets)
        has_text = any(cv.fitz_doc[p.id].get_text('text').strip() for p in targets)
        count = [0]

        def tick():
            count[0] += 1
            progress('extract', count[0], n)

        _tick = tick
        try:
            cv.parse_document(**settings)
        except Exception as e:
            # 这一步不受 ignore_page_error 保护，任何一页出错都会中断整份文档
            raise UserError('无法分析这个 PDF 的版面（%s），可以试试只转换部分页' % e)
        finally:
            _tick = None
        failed = []
        for i, page in enumerate(targets, 1):
            progress('parse', i, n)
            try:
                page.parse(**settings)
            except Exception:
                failed.append(page.id + 1)
        parsed = [p for p in targets if p.finalized]
        if not parsed:
            raise UserError('所有页面都转换失败，这个 PDF 可能不适合转成 Word')
        if opts.get('pages'):
            # 分析按原文顺序进行（页眉页脚要跨页比对），写入时按用户填写的页码顺序，与合并、拆分一致
            order = {pid: i for i, pid in enumerate(opts['pages'])}
            parsed.sort(key=lambda p: order.get(p.id, len(order)))
        doc = Document()
        for i, page in enumerate(parsed, 1):
            progress('write', i, len(parsed))
            try:
                page.make_docx(doc)
            except Exception:
                failed.append(page.id + 1)
        if len(set(failed)) >= n:
            raise UserError('所有页面都转换失败，这个 PDF 可能不适合转成 Word')
        doc.save(dst)
    finally:
        _tick = None
        cv.close()
    return json.dumps({'pages': n, 'failed': sorted(set(failed)), 'text': has_text})
`;

/** pages 为 0 起计的页序号（按此顺序写入），不传转换全部页；signal 中止时抛出 Cancelled */
export async function pdfToDocx(
  bytes: Uint8Array,
  opts: { pages?: number[] | null; signal?: AbortSignal } = {},
  onProgress?: (p: PyProgress & { stage: DocxStage }) => void,
) {
  const { data, meta } = await runPymupdf(
    {
      wheels: engineWheels('pdf2docx'),
      setup: PY,
      entry: 'bdw_to_docx',
      input: bytes,
      options: { pages: opts.pages?.length ? opts.pages : null },
    },
    onProgress as ((p: PyProgress) => void) | undefined,
    opts.signal,
  );
  return { blob: new Blob([data as BlobPart], { type: DOCX_TYPE }), meta: JSON.parse(meta) as DocxMeta };
}
