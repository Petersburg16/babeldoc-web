<script lang="ts">
  import { type Snippet, untrack } from 'svelte';
  import ProgressBar from '../../../components/ProgressBar.svelte';
  import { bytes, errorText, spaced } from '../../../lib/format';
  import { CircleAlert, LoaderCircle, X } from '../../../lib/icons';
  import { engines } from '../../../lib/pdf/engines.svelte';
  import type { OutputFile, Report } from '../../../lib/pdf/files';
  import { CloudDownload } from '../../../lib/pdf/icons';
  import { Cancelled } from '../../../lib/pdf/input';
  import { currentTool } from '../../../lib/pdf/tools';
  import ResultPanel from './ResultPanel.svelte';

  interface Props {
    runLabel: string;
    canRun: boolean;
    /** 不能运行时的提示，例如“至少选择两个文件” */
    blocked?: string;
    /**
     * 执行处理：用 report 报告进度（0–1，null 表示不定），返回生成的文件。
     * 用户点“停止”时 signal 会中止；能中途停下的工具应在收到后抛出 Cancelled，其余工具的结果会被丢弃。
     */
    onrun: (report: Report, signal: AbortSignal) => Promise<OutputFile[]>;
    input: Snippet;
    options?: Snippet;
    /** 结果上方的补充说明 */
    summary?: Snippet<[OutputFile[]]>;
    zipName?: string;
    /** “处理新文件”时清空输入 */
    onreset?: () => void;
    /** 选项放在文件下方（适合需要大面积展示的工具） */
    stacked?: boolean;
    /** 输入变化的标识（通常传所选文件）：变化时清掉上一次的结果和报错 */
    resetKey?: unknown;
  }

  let {
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
    resetKey,
  }: Props = $props();

  let phase = $state<'idle' | 'download' | 'run'>('idle');
  let fraction = $state<number | null>(null);
  let status = $state('');
  let error = $state('');
  let outputs = $state<OutputFile[]>([]);
  let controller: AbortController | null = null;

  /** 运行前要准备好的引擎（第一次会下载，之后读缓存），取自 tools.ts 的工具清单 */
  const needed = currentTool().engines;
  const pending = $derived(engines.pendingBytes(needed));
  const busy = $derived(phase !== 'idle');
  const engineLabel = $derived(spaced(engines.label(needed)));

  $effect(() => {
    void engines.refresh();
  });

  $effect(() => {
    void resetKey;
    untrack(() => {
      if (busy) return;
      outputs = [];
      error = '';
    });
  });

  $effect(() => () => controller?.abort());

  function message(e: unknown) {
    const raw = errorText(e);
    const name = e instanceof Error || e instanceof DOMException ? e.name : '';
    if (name === 'QuotaExceededError') return '浏览器存储空间不足，引擎无法缓存，请清理磁盘或浏览器数据后重试';
    if (e instanceof TypeError && /fetch|network|load failed/i.test(raw)) {
      return phase === 'download' ? '引擎下载失败：网络连接中断，请检查网络后重试' : '网络连接中断，请检查网络后重试';
    }
    return raw;
  }

  async function run() {
    if (busy || !canRun) return;
    error = '';
    outputs = [];
    const job = new AbortController();
    controller = job;
    const { signal } = job;
    const report: Report = (value, text) => {
      if (signal.aborted) return;
      fraction = value;
      if (text) status = text;
    };
    try {
      if (engines.pendingBytes(needed) > 0) {
        phase = 'download';
        fraction = 0;
        status = `正在下载${engineLabel}`;
        await engines.ensure(needed, (p) => report(p.total ? p.loaded / p.total : null), signal);
      } else {
        await engines.ensure(needed, undefined, signal);
      }
      if (signal.aborted) throw new Cancelled();
      phase = 'run';
      fraction = null;
      status = '正在处理…';
      const result = await onrun(report, signal);
      if (!signal.aborted) outputs = result;
    } catch (e) {
      const aborted = signal.aborted || (e instanceof DOMException && e.name === 'AbortError');
      if (!(e instanceof Cancelled) && !aborted) {
        console.error(e);
        error = message(e);
      }
    } finally {
      if (controller === job) {
        controller = null;
        phase = 'idle';
        fraction = null;
      }
    }
  }

  /** 停止：界面立即回到可操作状态；支持中止的工具会停下，其余工具的结果会被丢弃 */
  function stop() {
    controller?.abort();
    controller = null;
    phase = 'idle';
    fraction = null;
  }

  // 在单行输入框里按回车直接开始（输入法组字时的回车不算）
  function onkeydown(event: KeyboardEvent) {
    const target = event.target;
    if (event.key !== 'Enter' || event.isComposing || !(target instanceof HTMLInputElement)) return;
    if (['checkbox', 'radio', 'file', 'button', 'submit', 'range', 'color'].includes(target.type)) return;
    event.preventDefault();
    void run();
  }

  function reset() {
    outputs = [];
    error = '';
    onreset?.();
  }
</script>

<div class="grid gap-5 {stacked ? '' : 'lg:grid-cols-[minmax(0,1fr)_340px]'}">
  <div class="min-w-0">{@render input()}</div>

  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <aside class="{stacked ? '' : 'lg:sticky lg:top-20 lg:self-start'}" {onkeydown}>
    <div class="card p-4 sm:p-5">
      {#if options}
        <div class="space-y-4">{@render options()}</div>
      {/if}

      {#if pending >= 1_000_000 && phase !== 'download'}
        <p class="flex items-start gap-1.5 text-[12.5px] leading-snug text-muted {options ? 'mt-4' : ''}">
          <CloudDownload class="mt-px size-3.5 shrink-0" />
          首次使用需下载{engineLabel}，约 {bytes(pending)}，之后浏览器会缓存
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
          <div class="flex items-center gap-2">
            <p class="min-w-0 flex-1 text-[12.5px] text-muted">
              {status}{fraction !== null ? ` ${Math.round(fraction * 100)}%` : ''}
            </p>
            <button class="btn btn-ghost btn-sm -mr-1.5 shrink-0" onclick={stop}><X class="size-3.5" /> 停止</button>
          </div>
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
