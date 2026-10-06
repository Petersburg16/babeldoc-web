<script lang="ts">
  import FilePicker from '../../../components/FilePicker.svelte';
  import Segmented from '../../../components/Segmented.svelte';
  import { bytes } from '../../../lib/format';
  import type { FileFailure } from '../../../lib/pdf/engines/gs';
  import type { OutputFile, Report } from '../../../lib/pdf/files';
  import type { CompressLevel, CompressOutcome } from '../../../lib/pdf/ops/compress';
  import ToolFrame from '../ui/ToolFrame.svelte';

  const LEVELS: { value: CompressLevel; label: string; hint: string }[] = [
    { value: 'lossless', label: '无损', hint: '图片不动，用 qpdf 重新压缩文件结构；书签、表单、标签结构和密码都原样保留。体积通常只小一点。' },
    { value: 'printer', label: '高质量', hint: '图片降到 300 dpi，适合打印。' },
    { value: 'ebook', label: '标准', hint: '图片降到 150 dpi，屏幕阅读清晰，推荐。' },
    { value: 'screen', label: '强力', hint: '图片降到约 96 dpi，体积最小，放大后图片会模糊。' },
  ];

  let files = $state<File[]>([]);
  let level = $state<CompressLevel>('ebook');
  let results = $state<(CompressOutcome & { name: string })[]>([]);
  let failed = $state<FileFailure[]>([]);

  const current = $derived(LEVELS.find((l) => l.value === level)!);
  const totalBefore = $derived(results.reduce((n, r) => n + r.before, 0));
  const totalAfter = $derived(results.reduce((n, r) => n + r.after, 0));

  const saved = (before: number, after: number) => Math.max(0, Math.round((1 - after / before) * 100));

  async function run(report: Report) {
    // 运行中改选项不影响这一批
    const list = [...files];
    const chosen = level;
    const { compressFiles } = await import('../../../lib/pdf/ops/compress');
    results = [];
    failed = [];
    const { done, failed: errors } = await compressFiles(list, chosen, report);
    results = done;
    failed = errors;
    return done.map((r) => r.output) satisfies OutputFile[];
  }
</script>

<ToolFrame
  resetKey={files}
  runLabel={files.length > 1 ? `压缩 ${files.length} 个文件` : '压缩 PDF'}
  canRun={files.length > 0}
  blocked="请先选择 PDF"
  zipName="压缩结果.zip"
  onrun={run}
  onreset={() => {
    files = [];
    results = [];
    failed = [];
  }}
>
  {#snippet input()}
    <FilePicker bind:files accept="application/pdf,.pdf" multiple maxFiles={20} hint="可一次选择多个文件，逐个压缩；文字仍可选中和搜索" />
  {/snippet}

  {#snippet options()}
    <div>
      <span class="label">压缩程度</span>
      <Segmented bind:value={level} ariaLabel="压缩程度" options={LEVELS.map(({ value, label }) => ({ value, label }))} />
      <p class="hint">{current.hint}</p>
    </div>
    {#if level !== 'lossless'}
      <p class="rounded-lg bg-surface-2 px-3 py-2 text-[12.5px] leading-relaxed text-ink-2">
        这一档由 Ghostscript 重写整个文件：链接和书签一般会保留，但无障碍标签和可填写的表单会丢失，少数文件的书签也会丢。需要原样保留请选「无损」。
      </p>
    {/if}
  {/snippet}

  {#snippet summary()}
    {#if results.length > 1}
      <p class="mb-2">
        共 {results.length} 个文件：{@render change(totalBefore, totalAfter)}
      </p>
    {/if}
    <ul class="space-y-1.5">
      {#each results as r, i (i)}
        <li>
          {#if results.length > 1}
            <div class="flex items-baseline gap-3">
              <span class="min-w-0 flex-1 truncate text-ink" title={r.name}>{r.name}</span>
              <span class="shrink-0 tabular">{#if r.kept === 'larger'}没有变小，保留原文件{:else if r.kept}没有压缩，保留原文件{:else}{@render change(r.before, r.after)}{/if}</span>
            </div>
          {:else if r.kept === 'larger'}
            <p>压缩后没有变小，已保留原文件（{bytes(r.before)}）。</p>
          {:else if r.kept}
            <p>没有压缩，已保留原文件（{bytes(r.before)}）。</p>
          {:else}
            <p>{@render change(r.before, r.after)}</p>
          {/if}
          {#if r.warnings.length}
            <ul class="mt-1.5 space-y-1">
              {#each r.warnings as warning (warning)}
                <li class="rounded-lg bg-warn-soft px-3 py-1.5 text-[12.5px] text-warn-ink">{warning}</li>
              {/each}
            </ul>
          {/if}
        </li>
      {/each}
    </ul>
    {#if failed.length}
      <p class="mt-3 mb-1.5">{failed.length} 个文件没能压缩：</p>
      <ul class="space-y-1">
        {#each failed as f, i (i)}
          <li class="rounded-lg bg-bad-soft px-3 py-1.5 text-[12.5px] text-bad-ink">「{f.name}」{f.message}</li>
        {/each}
      </ul>
    {/if}
  {/snippet}
</ToolFrame>

{#snippet change(before: number, after: number)}
  {bytes(before)} → <b class="font-semibold text-ink">{bytes(after)}</b>{after < before ? `，减小 ${saved(before, after)}%` : ''}
{/snippet}
