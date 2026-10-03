// Office 转 PDF 用到的中文字体：每个文件是 pdf-assets.lock.json 里的一个引擎，和水印、页码等工具共用缓存。
// 按用途分三组：有中文就装思源黑体、思源宋体（含粗体）；文档点名楷体、仿宋时再加霞鹜文楷、朱雀仿宋。
import type { EngineId } from '../engines.svelte';

export type FontKey = 'cjk' | 'kai' | 'fang';

export const ALL_FONT_KEYS: FontKey[] = ['cjk', 'kai', 'fang'];

export const FONT_FILES: Record<FontKey, { id: EngineId; file: string }[]> = {
  cjk: [
    { id: 'font-sans', file: 'SourceHanSansCN-Regular.ttf' },
    { id: 'font-sans-bold', file: 'SourceHanSansCN-Bold.ttf' },
    { id: 'font-serif', file: 'SourceHanSerifCN-Regular.ttf' },
    { id: 'font-serif-bold', file: 'SourceHanSerifCN-Bold.ttf' },
  ],
  kai: [{ id: 'font-kai', file: 'LXGWWenKaiGB-Regular.1.520.ttf' }],
  fang: [{ id: 'font-fang', file: 'ZhuqueFangsong-Regular.ttf' }],
};

export function fontFiles(keys: Iterable<FontKey>) {
  return [...new Set(keys)].flatMap((key) => FONT_FILES[key]);
}

export function fontEngineIds(keys: Iterable<FontKey>): EngineId[] {
  return fontFiles(keys).map((f) => f.id);
}
