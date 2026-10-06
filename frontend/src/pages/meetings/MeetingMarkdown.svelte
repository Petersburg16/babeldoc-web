<script lang="ts">
  import { renderMarkdown } from '../../lib/meeting/markdown';
  import type { SpeakerInfo } from '../../lib/meeting/types';

  // 纪要和对话回答共用的 Markdown 显示：统一排版，时间戳按钮用事件委托交给 onseek
  interface Props {
    source: string;
    speakers: Record<string, SpeakerInfo>;
    durationMs?: number;
    onseek: (ms: number) => void;
  }

  let { source, speakers, durationMs, onseek }: Props = $props();

  // 改名、合并时 speakers 会变，这里跟着重新渲染，不用重新拉纪要
  const html = $derived(renderMarkdown(source, speakers, durationMs));

  function onclick(event: MouseEvent) {
    const button = (event.target as Element | null)?.closest<HTMLButtonElement>('button[data-seek]');
    if (!button) return;
    const ms = Number(button.dataset.seek);
    if (Number.isFinite(ms)) onseek(ms);
  }
</script>

<!-- 点击来自内部的真按钮，键盘可达；外层 div 只负责转发 -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="md" {onclick}>
  {@html html}
</div>

<style>
  .md {
    color: var(--ink);
    font-size: 14px;
    line-height: 1.75;
    overflow-wrap: anywhere;
  }
  .md > :global(:first-child) {
    margin-top: 0;
  }
  .md > :global(:last-child) {
    margin-bottom: 0;
  }
  .md :global(h1) {
    margin: 0 0 0.75em;
    font-size: 18px;
    font-weight: 600;
    line-height: 1.4;
    letter-spacing: -0.01em;
  }
  .md :global(h2) {
    margin: 1.6em 0 0.6em;
    padding-bottom: 0.35em;
    border-bottom: 1px solid var(--line);
    font-size: 15.5px;
    font-weight: 600;
    line-height: 1.45;
  }
  .md :global(h3) {
    margin: 1.3em 0 0.4em;
    font-size: 14.5px;
    font-weight: 600;
    line-height: 1.5;
  }
  .md :global(h4),
  .md :global(h5),
  .md :global(h6) {
    margin: 1.1em 0 0.3em;
    font-size: 14px;
    font-weight: 600;
    color: var(--ink-2);
  }
  .md :global(p) {
    margin: 0.55em 0;
  }
  .md :global(ul),
  .md :global(ol) {
    margin: 0.5em 0;
    padding-left: 1.4em;
  }
  .md :global(ul) {
    list-style: disc;
  }
  .md :global(ol) {
    list-style: decimal;
  }
  .md :global(li) {
    margin: 0.25em 0;
    padding-left: 0.15em;
  }
  .md :global(li > ul),
  .md :global(li > ol) {
    margin: 0.2em 0;
  }
  .md :global(li > p) {
    margin: 0.2em 0;
  }
  .md :global(li::marker) {
    color: var(--muted);
  }
  .md :global(strong) {
    font-weight: 600;
    color: var(--ink);
  }
  .md :global(a) {
    color: var(--accent-ink);
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .md :global(blockquote) {
    margin: 0.75em 0;
    padding: 0.1em 0 0.1em 0.9em;
    border-left: 3px solid var(--line-strong);
    color: var(--ink-2);
  }
  .md :global(hr) {
    margin: 1.4em 0;
    border: 0;
    border-top: 1px solid var(--line);
  }
  .md :global(code) {
    padding: 0.1em 0.35em;
    border-radius: 5px;
    background: var(--surface-2);
    font-family: ui-monospace, 'SF Mono', Menlo, 'Cascadia Mono', Consolas, monospace;
    font-size: 0.88em;
  }
  .md :global(pre) {
    margin: 0.75em 0;
    padding: 0.75em 1em;
    overflow-x: auto;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--surface-2);
    line-height: 1.6;
  }
  .md :global(pre code) {
    padding: 0;
    background: none;
  }
  .md :global(.md-table) {
    margin: 0.85em 0;
    overflow-x: auto;
    border: 1px solid var(--line);
    border-radius: 10px;
  }
  .md :global(table) {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    line-height: 1.6;
  }
  .md :global(th),
  .md :global(td) {
    min-width: 5.5em;
    padding: 0.5em 0.75em;
    border-left: 1px solid var(--line);
    text-align: left;
    vertical-align: top;
  }
  .md :global(th) {
    background: var(--surface-2);
    font-weight: 600;
    color: var(--ink-2);
    white-space: nowrap;
  }
  .md :global(td) {
    border-top: 1px solid var(--line);
  }
  .md :global(tr > :first-child) {
    border-left: 0;
  }
  .md :global(.md-seek) {
    display: inline-block;
    margin: 0 0.15em;
    padding: 0 0.4em;
    border-radius: 6px;
    background: var(--accent-soft);
    color: var(--accent-ink);
    font-size: 0.85em;
    font-weight: 500;
    line-height: 1.6;
    font-variant-numeric: tabular-nums;
    vertical-align: baseline;
    white-space: nowrap;
    transition:
      background-color 0.15s,
      color 0.15s;
  }
  .md :global(.md-seek:hover) {
    background: var(--accent);
    color: #fff;
  }
</style>
