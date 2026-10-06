import MarkdownIt, { type StateCore, type Token } from 'markdown-it';
import { fillSpeakers, parseClock, speakerName } from './format';
import type { SpeakerInfo } from './types';

interface RenderEnv {
  speakers: Record<string, SpeakerInfo>;
  /** 会议时长；超出的时间戳不做成按钮（大模型偶尔会编出不存在的时间） */
  durationMs?: number;
}

// 时间戳 [hh:mm:ss] / [mm:ss] 和说话人占位符（写法同 format.ts 的 SPEAKER_PLACEHOLDER）一起扫描；
// 大模型偶尔会写成区间 [12:30–15:00]，也认出来，跳到起点
const MARK = /\[\[(S\d+)\]\]|\[(\d{1,2}:\d{2}(?::\d{2})?)(?:\s*[-–—~～]\s*(\d{1,2}:\d{2}(?::\d{2})?))?\]/g;
// 时长有误差（取整、转码），留一点余量再判定“超出会议时长”
const DURATION_SLACK_MS = 5000;

const md = new MarkdownIt({ html: false, breaks: true });
// 站点开了跨源隔离，也不允许任何第三方请求：纪要里的外链图片既显示不了，也不该去拉
md.disable('image');

function marks(state: StateCore) {
  const env = state.env as RenderEnv | undefined;
  if (!env?.speakers) return;
  const { speakers, durationMs } = env;
  const names = (text: string) => fillSpeakers(text, speakers);

  for (const block of state.tokens) {
    // 代码里的内容原样显示，只把占位符换成名字，不做时间戳按钮
    if (block.type === 'fence' || block.type === 'code_block') {
      block.content = names(block.content);
      continue;
    }
    if (block.type !== 'inline' || !block.children) continue;
    const out: Token[] = [];
    let linkDepth = 0;
    for (const token of block.children) {
      if (token.type === 'link_open') linkDepth++;
      else if (token.type === 'link_close') linkDepth = Math.max(0, linkDepth - 1);
      if (token.type === 'code_inline') token.content = names(token.content);
      if (token.type !== 'text' || !token.content.includes('[')) {
        out.push(token);
        continue;
      }
      let last = 0;
      let buffer = '';
      const flush = () => {
        if (!buffer) return;
        const text = new state.Token('text', '', 0);
        text.content = buffer;
        text.level = token.level;
        out.push(text);
        buffer = '';
      };
      for (const match of token.content.matchAll(MARK)) {
        buffer += token.content.slice(last, match.index);
        last = match.index + match[0].length;
        if (match[1]) {
          buffer += speakerName(speakers, match[1]);
          continue;
        }
        const ms = parseClock(match[2]);
        const end = match[3] ? parseClock(match[3]) : ms;
        // 链接里不能再放按钮（交互元素不能嵌套）
        if (ms === null || end === null || linkDepth > 0 || (durationMs && ms > durationMs + DURATION_SLACK_MS)) {
          buffer += match[0];
          continue;
        }
        flush();
        const seek = new state.Token('meeting_seek', '', 0);
        seek.meta = { ms, start: match[2], label: match[3] ? `${match[2]}–${match[3]}` : match[2] };
        seek.level = token.level;
        out.push(seek);
      }
      buffer += token.content.slice(last);
      flush();
    }
    block.children = out;
  }
}

md.core.ruler.after('text_join', 'meeting_marks', marks);

md.renderer.rules.meeting_seek = (tokens, idx) => {
  const { ms, start, label } = tokens[idx].meta as { ms: number; start: string; label: string };
  return `<button type="button" class="md-seek" data-seek="${ms}" title="跳到 ${start}">${md.utils.escapeHtml(label)}</button>`;
};

const renderToken = md.renderer.renderToken.bind(md.renderer);

md.renderer.rules.link_open = (tokens, idx, options) => {
  tokens[idx].attrSet('target', '_blank');
  tokens[idx].attrSet('rel', 'noopener noreferrer nofollow');
  return renderToken(tokens, idx, options);
};

// 表格外面套一层，手机上可以横向滚动
md.renderer.rules.table_open = (tokens, idx, options) => `<div class="md-table">${renderToken(tokens, idx, options)}`;
md.renderer.rules.table_close = (tokens, idx, options) => `${renderToken(tokens, idx, options)}</div>`;

/**
 * 把纪要或对话回答渲染成 HTML：不允许原始 HTML，说话人占位符 [[S3]] 换成显示名，
 * 时间戳 [hh:mm:ss] / [mm:ss] 变成 <button data-seek="毫秒">，由外层用事件委托处理点击。
 * 替换在 markdown-it 解析之后、按文本节点进行，名字里有 *、_、| 之类的符号也不会被当成 Markdown 语法。
 */
export function renderMarkdown(source: string, speakers: Record<string, SpeakerInfo>, durationMs?: number) {
  const env: RenderEnv = { speakers, durationMs };
  return md.render(source, env);
}
