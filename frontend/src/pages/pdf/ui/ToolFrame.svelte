<script lang="ts">
  import type { Snippet } from 'svelte';
  import ProgressBar from '../../../components/ProgressBar.svelte';
  import { bytes } from '../../../lib/format';
  import { CircleAlert, LoaderCircle } from '../../../lib/icons';
  import { type EngineId, engines } from '../../../lib/pdf/engines.svelte';
  import type { OutputFile, Report } from '../../../lib/pdf/files';
  import { CloudDownload } from '../../../lib/pdf/icons';
  import { Cancelled } from '../../../lib/pdf/input';
  import ResultPanel from './ResultPanel.svelte';

  interface Props {
    /** 运行前要准备好的引擎（第一次会下载，之后读缓存） */
    engines: EngineId[];
    runLabel: string;
    canRun: boolean;
    /** 不能运行时的提示，例如“至少选择两个文件” */
    blocked?: string;
    /** 执行处理：用 report 报告进度（0–1，null 表示不定），返回生成的文件 */
    onrun: (report: Report) => Promise<OutputFile[]>;
    input: Snippet;
    options?: Snippet;
    /** 结果上方的补充说明 */
    summary?: Snippet<[OutputFile[]]>;
    zipName?: string;
    /** “处理新文件”时清空输入 */
    onreset?: () => void;
    /** 选项放在文件下方（适合需要大面积展示的工具） */
    stacked?: boolean;
  }

  let {
    engines: needed,
    runLabel,
    canRun,
    blocked = '',
    onrun,
    input,
    options,
    summary: summaryContent,
    zipName,
    onreset,
    stacked = false,
  }: Props = $props();

  let phase = $state<'idle' | 'download' | 'run'>('idle');
  let fraction = $state<number | null>(null);
  let status = $state('');
  let error = $state('');
  let outputs = $state<OutputFile[]>([]);

  const pending = $derived(engines.pendingBytes(needed));
  const busy = $derived(phase !== 'idle');

  $effect(() => {
    void engines.refresh();
  });

  async function run() {
    if (busy || !canRun) return;
    error = '';
    outputs = [];
    try {
      if (engines.pendingBytes(needed) > 0) {
        phase = 'download';
        fraction = 0;
        status = `正在下载${engines.label(needed)}`;
        await engines.ensure(needed, (p) => {
          fraction = p.total ? p.loaded / p.total : null;
        });
      } else {
        await engines.ensure(needed);
      }
      phase = 'run';
      fraction = null;
      status = '正在处理…';
      outputs = await onrun((value, text) => {
        fraction = value;
        if (text) status = text;
      });
    } catch (e) {
      if (!(e instanceof Cancelled)) {
        console.error(e);
        error = e instanceof Error ? e.message : String(e);
      }
    } finally {
      phase = 'idle';
      fraction = null;
    }
  }

  function reset() {
    outputs = [];
    error = '';
    onreset?.();
  }
</script>

<div class="grid gap-5 {stacked ? '' : 'lg:grid-cols-[minmax(0,1fr)_340px]'}">
  <div class="min-w-0">{@render input()}</div>

  <aside class="{stacked ? '' : 'lg:sticky lg:top-20 lg:self-start'}">
    <div class="card p-4 sm:p-5">
      {#if options}
        <div class="space-y-4">{@render options()}</div>
      {/if}

      {#if pending >= 1_000_000 && phase !== 'download'}
        <p class="flex items-start gap-1.5 text-[12.5px] leading-snug text-muted {options ? 'mt-4' : ''}">
          <CloudDownload class="mt-px size-3.5 shrink-0" />
          首次使用需下载{engines.label(needed)}，约 {bytes(pending)}，之后浏览器会缓存
        </p>
      {/if}

      <button class="btn btn-primary btn-lg mt-4 w-full" disabled={!canRun || busy} onclick={run}>
        {#if busy}<LoaderCircle class="size-4 animate-spin" />{/if}
        {busy ? (phase === 'download' ? '正在下载引擎…' : '正在处理…') : runLabel}
      </button>
      {#if !canRun && blocked && !busy}
        <p class="mt-2 text-center text-[12.5px] text-muted">{blocked}</p>
      {/if}

      {#if busy}
        <div class="mt-3 space-y-1.5" aria-live="polite">
          <ProgressBar value={fraction === null ? 100 : fraction * 100} active={fraction === null} />
          <p class="text-[12.5px] text-muted">
            {status}{fraction !== null ? ` ${Math.round(fraction * 100)}%` : ''}
          </p>
        </div>
      {/if}

      {#if error}
        <div class="mt-3 flex items-start gap-2 rounded-lg bg-bad-soft px-3 py-2 text-[13px] text-bad-ink" role="alert">
          <CircleAlert class="mt-0.5 size-4 shrink-0" />
          <span class="min-w-0 break-words">{error}</span>
        </div>
      {/if}
    </div>
  </aside>
</div>

{#snippet summarize()}
  {@render summaryContent?.(outputs)}
{/snippet}

{#if outputs.length}
  <div class="mt-5">
    <ResultPanel {outputs} {zipName} onreset={reset} summary={summaryContent ? summarize : undefined} />
  </div>
{/if}
