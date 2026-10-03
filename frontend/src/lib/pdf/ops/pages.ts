// 页面类操作（合并、拆分、提取、旋转、整理）统一用 qpdf 完成：不重写页面内容，体积小，文档结构保留得多。
// qpdf 页码写法：1 起计，z 表示末页，r1 表示倒数第一页。
import { runQpdf, QpdfError } from '../engines/qpdf';
import { parseRanges } from '../ranges';

/** 页数（文件需未加密，或传入打开密码） */
export async function countPages(bytes: Uint8Array, password?: string) {
  const pw = password ? [`--password=${password}`] : [];
  const r = await runQpdf([...pw, '--show-npages', '/in.pdf'], { 'in.pdf': bytes }, []);
  const n = Number.parseInt(r.log.trim().split('\n').pop() ?? '', 10);
  if (!Number.isFinite(n) || n < 1) throw new QpdfError(r.code, '读不出页数');
  return n;
}

/** 把本站的页码写法（"1-3,5,8-"）换成 qpdf 的写法，并按总页数校验 */
export function toQpdfRange(spec: string, total: number) {
  return parseRanges(spec, total)
    .map(({ start, end }) => (start === end ? `${start}` : `${start}-${end}`))
    .join(',');
}

export interface MergeInput {
  bytes: Uint8Array;
  /** 本站写法的页码范围，空表示全部 */
  pages?: string;
  pageCount: number;
  password?: string;
}

/** 按顺序合并；每个文件可只取部分页。加密文件用各自的密码读入，结果不加密。 */
export async function mergePdfs(inputs: MergeInput[]) {
  const args = ['--empty', '--pages'];
  const files: Record<string, Uint8Array> = {};
  inputs.forEach((input, i) => {
    const name = `in${i}.pdf`;
    files[name] = input.bytes;
    args.push(`/${name}`);
    if (input.password) args.push(`--password=${input.password}`);
    args.push(input.pages?.trim() ? toQpdfRange(input.pages, input.pageCount) : '1-z');
  });
  args.push('--', '/out.pdf');
  const r = await runQpdf(args, files);
  return r.files['out.pdf'];
}
