// 移植自 BentoPDF（AGPL-3.0）src/js/logic/split-pdf-page.ts、extract-pages-page.ts、rotate-pdf-page.ts，按本站引擎与界面重写
// 拆分、提取、旋转都用 qpdf：只搬运页面对象，不重写内容；共享的字体等资源不会在每份里各复制一遍。
import { engines } from '../engines.svelte';
import { encryptionInfo, QpdfError, retryOnCrash, runQpdf } from '../engines/qpdf';
import { type OutputFile, pdfBlob, readBytes, type Report, stem } from '../files';
import { compactPages, expandRanges, formatRanges, parseRanges } from '../ranges';
import { countPages } from './pages';

const COMMON = ['--remove-unreferenced-resources=yes', '--object-streams=generate'];
/** 选文件时顺手读页数的上限：再大就等开始处理时再读，免得多占一份内存 */
const INSPECT_MAX = 200 * 1024 * 1024;

export interface PdfInfo {
  /** 需要打开密码或文件读不出来时没有页数 */
  pages?: number;
  locked: boolean;
  broken: boolean;
}

/** 选好文件后先看一眼：是否要密码、有几页。离线、文件太大时返回 null，不影响后续处理 */
export async function inspectPdf(file: File): Promise<PdfInfo | null> {
  if (file.size > INSPECT_MAX) return null;
  try {
    await engines.ensure(['qpdf']);
  } catch {
    return null;
  }
  try {
    const bytes = await readBytes(file);
    return await retryOnCrash(async () => {
      if ((await encryptionInfo(bytes)).needsPassword) return { locked: true, broken: false };
      return { pages: await countPages(bytes), locked: false, broken: false };
    });
  } catch {
    return { locked: false, broken: true };
  }
}

const qpdf = (args: string[], inputs: Record<string, Uint8Array>, outputs?: string[] | null) =>
  retryOnCrash(() => runQpdf(args, inputs, outputs));

// ---- 书签：qpdf 用 --pages 取部分页时保留整棵书签树，指向已删除页的书签点了没反应，这里按每份的页面修剪 ----

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Dict = { [key: string]: Json };

interface OutlineItem {
  /** 对象号，如 "4 0 R" */
  id: string;
  /** 指向的页（0 起计）；网址等不指向本文件页面、或 qpdf 解析不出时为 null，这类书签一律保留 */
  page: number | null;
  open: boolean;
  kids: OutlineItem[];
}

interface Outline {
  header: Dict;
  items: OutlineItem[];
  dicts: Map<string, Dict>;
  catalogId: string;
  catalog: Dict;
  rootId: string;
  root: Dict;
}

const isDict = (v: unknown): v is Dict => typeof v === 'object' && v !== null && !Array.isArray(v);

function toItems(raw: unknown): OutlineItem[] | null {
  if (!Array.isArray(raw)) return null;
  const items: OutlineItem[] = [];
  for (const o of raw) {
    const kids = isDict(o) ? toItems(o.kids) : null;
    if (!kids || typeof o.object !== 'string') return null;
    const pos = o.destpageposfrom1;
    items.push({ id: o.object, page: typeof pos === 'number' ? pos - 1 : null, open: o.open !== false, kids });
  }
  return items;
}

const flatten = (items: OutlineItem[]): OutlineItem[] => items.flatMap((i) => [i, ...flatten(i.kids)]);

/**
 * qpdf 的 JSON 一律写进文件再读回：写到标准输出时，若同一个模块里之前有命令输出过（如 --show-npages），
 * qpdf 会报 “called setSave on standard output after standard output has already been used”。
 */
async function qpdfJson(bytes: Uint8Array, args: string[]): Promise<unknown> {
  const r = await qpdf(['/in.pdf', ...args, '/o.json'], { 'in.pdf': bytes }, ['o.json']);
  const out = r.files['o.json'];
  if (!out) throw new QpdfError(r.code, r.log || '没有生成文件');
  return JSON.parse(new TextDecoder().decode(out));
}

/** 读出指定对象（trailer 或 "4 0 R"） */
async function readObjects(bytes: Uint8Array, ids: string[]) {
  const args = ['--json-output', '--json-stream-data=none', ...ids.map((id) => `--json-object=${id}`)];
  const [header, objects] = ((await qpdfJson(bytes, args)) as { qpdf: [Dict, Dict] }).qpdf;
  const get = (id: string) => {
    const o = objects[id === 'trailer' ? id : `obj:${id}`];
    return isDict(o) && isDict(o.value) ? o.value : null;
  };
  return { header, get };
}

/** 读书签树和改写它要用到的对象；没有书签或读取失败时返回 null（照 qpdf 原样输出） */
async function readOutline(bytes: Uint8Array): Promise<Outline | null> {
  try {
    const json = (await qpdfJson(bytes, ['--json=2', '--json-key=outlines'])) as { outlines?: unknown };
    const items = toItems(json.outlines);
    if (!items?.length) return null;

    const all = flatten(items);
    const first = await readObjects(bytes, ['trailer', ...all.map((i) => i.id)]);
    const dicts = new Map<string, Dict>();
    for (const item of all) {
      const d = first.get(item.id);
      if (!d || dicts.has(item.id)) return null;
      dicts.set(item.id, d);
    }
    const catalogId = first.get('trailer')?.['/Root'];
    const rootId = dicts.get(items[0].id)?.['/Parent'];
    if (typeof catalogId !== 'string' || typeof rootId !== 'string') return null;
    const second = await readObjects(bytes, [catalogId, rootId]);
    const catalog = second.get(catalogId);
    const root = second.get(rootId);
    if (!catalog || !root || catalog['/Outlines'] !== rootId) return null;
    return { header: first.header, items, dicts, catalogId, catalog, rootId, root };
  } catch {
    return null;
  }
}

interface Kept {
  item: OutlineItem;
  kids: Kept[];
  /** 跳转目标取自哪个书签：自己指向的页不在这份里时，借用第一个留下的子书签的 */
  target: string;
}

function keep(items: OutlineItem[], pages: Set<number>): Kept[] {
  const kept: Kept[] = [];
  for (const item of items) {
    const kids = keep(item.kids, pages);
    const own = item.page === null || pages.has(item.page);
    if (own || kids.length) kept.push({ item, kids, target: own ? item.id : kids[0].target });
  }
  return kept;
}

/** 生成 qpdf --update-from-json 用的文件：只留指向 pages（0 起计）里的书签；一条都不用改时返回 null */
function outlineUpdate(o: Outline, pages: Set<number>): Uint8Array | null {
  const dangling = (items: OutlineItem[]): boolean =>
    items.some((i) => (i.page !== null && !pages.has(i.page)) || dangling(i.kids));
  if (!dangling(o.items)) return null;

  const kept = keep(o.items, pages);
  const objects: Record<string, { value: Json }> = {};
  // 重新串起 Parent / Prev / Next / First / Last，返回这一层展开后可见的书签数（用于 /Count）
  const link = (list: Kept[], parent: string): number => {
    let visible = 0;
    list.forEach((k, i) => {
      const d: Dict = { ...o.dicts.get(k.item.id) };
      for (const key of ['/Prev', '/Next', '/First', '/Last', '/Count']) delete d[key];
      d['/Parent'] = parent;
      if (i > 0) d['/Prev'] = list[i - 1].item.id;
      if (i < list.length - 1) d['/Next'] = list[i + 1].item.id;
      if (k.target !== k.item.id) {
        const t = o.dicts.get(k.target)!;
        delete d['/Dest'];
        delete d['/A'];
        if (t['/Dest'] !== undefined) d['/Dest'] = t['/Dest'];
        else if (t['/A'] !== undefined) d['/A'] = t['/A'];
      }
      const below = link(k.kids, k.item.id);
      if (k.kids.length) {
        d['/First'] = k.kids[0].item.id;
        d['/Last'] = k.kids[k.kids.length - 1].item.id;
        // 正数表示展开，负数表示折叠，保持原来的展开状态
        d['/Count'] = k.item.open ? below : -below;
      }
      objects[`obj:${k.item.id}`] = { value: d };
      visible += 1 + (k.item.open ? below : 0);
    });
    return visible;
  };

  if (kept.length) {
    const count = link(kept, o.rootId);
    objects[`obj:${o.rootId}`] = {
      value: { ...o.root, '/First': kept[0].item.id, '/Last': kept[kept.length - 1].item.id, '/Count': count },
    };
  } else {
    // 一条都不剩：去掉书签，也不再让阅读器打开时弹出空的书签栏
    const catalog: Dict = { ...o.catalog };
    delete catalog['/Outlines'];
    if (catalog['/PageMode'] === '/UseOutlines') delete catalog['/PageMode'];
    objects[`obj:${o.catalogId}`] = { value: catalog };
  }
  const { jsonversion, pushedinheritedpageresources, calledgetallpages, maxobjectid } = o.header;
  const header = { jsonversion, pushedinheritedpageresources, calledgetallpages, maxobjectid };
  return new TextEncoder().encode(JSON.stringify({ qpdf: [header, objects] }));
}

/** 从 bytes 里取页（qpdf 写法的 range），书签按 pages 修剪；Info 等文档属性随之保留 */
async function pickPages(bytes: Uint8Array, range: string, outline: Outline | null, pages: Iterable<number>) {
  const update = outline && outlineUpdate(outline, new Set(pages));
  const inputs: Record<string, Uint8Array> = { 'in.pdf': bytes };
  const args = ['/in.pdf', ...COMMON];
  if (update) {
    inputs['u.json'] = update;
    args.push('--update-from-json=/u.json');
  }
  return onlyOutput(await qpdf([...args, '--pages', '.', range, '--', '/out.pdf'], inputs));
}

function onlyOutput(r: { code: number; log: string; files: Record<string, Uint8Array> }) {
  const out = r.files['out.pdf'];
  if (!out) throw new QpdfError(r.code, r.log || '没有生成文件');
  return out;
}

export type SplitMode = 'ranges' | 'every' | 'single' | 'extract';

export interface SplitOptions {
  mode: SplitMode;
  /** 按范围 / 提取：本站页码写法 */
  spec: string;
  /** 每 N 页一份 */
  every: number;
}

/** 文件名里的页码：第3页、第1-3页 */
const pageLabel = (a: number, b: number) => (a === b ? `第${a}页` : `第${a}-${b}页`);

const span = (start: number, end: number) => Array.from({ length: end - start + 1 }, (_, i) => start - 1 + i);

/** bytes 需未加密（先用 input.ts 的 readUserPdf 读入） */
export async function splitPdf(
  name: string,
  bytes: Uint8Array,
  total: number,
  { mode, spec, every }: SplitOptions,
  report: Report,
): Promise<OutputFile[]> {
  const base = stem(name);

  if (mode === 'single' || mode === 'every') {
    const n = mode === 'single' ? 1 : Math.max(1, Math.floor(every));
    report(null, '正在拆分');
    // 一次解析、一次写出全部分卷，几百页也只要几秒；qpdf 按总页数补零命名：part-1.pdf、part-01-04.pdf。
    // 代价是分卷不带书签和 Info；逐段用 --pages 能保留，但慢十倍以上，页数多时还会碰上 qpdf-wasm 的崩溃
    const r = await qpdf(['/in.pdf', ...COMMON, `--split-pages=${n}`, '/part-%d.pdf'], { 'in.pdf': bytes }, null);
    const parts = Object.entries(r.files)
      .map(([file, data]) => {
        const m = /^part-(\d+)(?:-(\d+))?\.pdf$/.exec(file);
        return m ? { first: Number(m[1]), last: Number(m[2] ?? m[1]), data } : null;
      })
      .filter((p) => p !== null)
      .sort((a, b) => a.first - b.first);
    if (!parts.length) throw new QpdfError(r.code, r.log || '没有生成文件');
    return parts.map((p) => ({ name: `${base}-${pageLabel(p.first, p.last)}.pdf`, blob: pdfBlob(p.data) }));
  }

  if (mode === 'extract') {
    // 去掉重复页，按填写顺序排列：[4,0,1,2] → "5,1-3"
    const indices = expandRanges(spec, total);
    const range = compactPages(indices.map((i) => i + 1));
    report(null, '正在提取');
    const out = await pickPages(bytes, range, await readOutline(bytes), indices);
    // 页码太零碎时不放进文件名
    const suffix = range.length <= 24 ? `第${range}页` : '提取';
    return [{ name: `${base}-${suffix}.pdf`, blob: pdfBlob(out) }];
  }

  // 先校验页码（加密文件解开后才知道页数），再读书签
  const groups = parseRanges(spec, total);
  report(0, '正在读取书签');
  const outline = await readOutline(bytes);
  const outputs: OutputFile[] = [];
  for (const [i, { start, end }] of groups.entries()) {
    const range = start === end ? `${start}` : `${start}-${end}`;
    report(i / groups.length, `正在生成第 ${range} 页`);
    const out = await pickPages(bytes, range, outline, span(start, end));
    outputs.push({ name: `${base}-${pageLabel(start, end)}.pdf`, blob: pdfBlob(out) });
  }
  return outputs;
}

export type Angle = 90 | 180 | 270;

/**
 * 顺时针旋转（PDF 的 /Rotate 以顺时针为正），在页面原有角度上累加；spec 为空时旋转全部页。
 * qpdf 对范围里重复出现的页会转两次（实测 "1,1" 得到 180°），所以先去重排序。bytes 需未加密。
 */
export async function rotatePdf(bytes: Uint8Array, total: number, angle: Angle, spec: string) {
  const range = spec.trim() ? `:${formatRanges(expandRanges(spec, total))}` : '';
  return onlyOutput(await qpdf(['/in.pdf', `--rotate=+${angle}${range}`, '/out.pdf'], { 'in.pdf': bytes }));
}
