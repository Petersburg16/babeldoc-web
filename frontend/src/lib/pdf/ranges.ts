// 页码范围与翻译页（backend/app/pages.py）一致：1 起计，逗号分隔，"3-" 到末页，"-5" 从首页；全角逗号和破折号也认。
import { errorText } from '../format';

export interface PageRange {
  start: number;
  end: number;
}

const TOKEN = /^(\d*)-(\d*)$|^(\d+)$/;

export function normalizeRanges(spec: string) {
  return spec.replace(/\s+/g, '').replaceAll('，', ',').replace(/[–—]/g, '-');
}

/** 解析为 1 起计的闭区间，末页按 total 截断；超出总页数或写法有误时抛出中文错误。 */
export function parseRanges(spec: string, total: number): PageRange[] {
  const cleaned = normalizeRanges(spec);
  if (!cleaned) throw new Error('请输入页码');
  return cleaned.split(',').map((token) => {
    const m = TOKEN.exec(token);
    if (!m || token === '-') throw new Error(`无法识别的页码：${token || '（空）'}`);
    let start: number;
    let end: number;
    if (m[3]) {
      start = end = Number(m[3]);
    } else {
      start = m[1] ? Number(m[1]) : 1;
      end = m[2] ? Number(m[2]) : total;
    }
    if (start < 1) throw new Error(`页码范围无效：${token}`);
    if (start > total) throw new Error(`第 ${start} 页超出总页数（共 ${total} 页）`);
    if (end < start) throw new Error(`页码范围无效：${token}`);
    return { start, end: Math.min(end, total) };
  });
}

/** 按书写顺序展开为 0 起计的页序号，重复的页只保留第一次出现。 */
export function expandRanges(spec: string, total: number): number[] {
  const seen = new Set<number>();
  const out: number[] = [];
  for (const { start, end } of parseRanges(spec, total)) {
    for (let p = start; p <= end; p++) {
      if (!seen.has(p - 1)) {
        seen.add(p - 1);
        out.push(p - 1);
      }
    }
  }
  return out;
}

/** 校验但不抛出：返回错误信息，合法时返回空字符串（total 未知时只查写法）。 */
export function rangeError(spec: string, total = Number.MAX_SAFE_INTEGER) {
  try {
    parseRanges(spec, total);
    return '';
  } catch (e) {
    return errorText(e);
  }
}

/**
 * 按给定顺序把 1 起计的页号压成范围写法，连续递增的合并为 a-b：[5,1,2,3] → "5,1-3"。
 * 不排序、不去重：qpdf 按写出的顺序取页，提取和整理要保留用户排的顺序。
 */
export function compactPages(pages: number[]) {
  const parts: string[] = [];
  for (let i = 0; i < pages.length; i++) {
    const start = pages[i];
    while (i + 1 < pages.length && pages[i + 1] === pages[i] + 1) i++;
    parts.push(start === pages[i] ? `${start}` : `${start}-${pages[i]}`);
  }
  return parts.join(',');
}

/** 把 0 起计的页序号压缩回范围写法，例如 [0,1,2,4] → "1-3,5"。 */
export function formatRanges(indices: number[]) {
  return compactPages([...new Set(indices)].sort((a, b) => a - b).map((i) => i + 1));
}
