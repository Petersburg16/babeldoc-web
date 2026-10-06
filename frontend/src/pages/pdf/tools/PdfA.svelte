<script lang="ts">
  import FilePicker from '../../../components/FilePicker.svelte';
  import Segmented from '../../../components/Segmented.svelte';
  import Switch from '../../../components/Switch.svelte';
  import { TriangleAlert } from '../../../lib/icons';
  import type { FileFailure } from '../../../lib/pdf/engines/gs';
  import type { OutputFile, Report } from '../../../lib/pdf/files';
  import type { PdfALevel, PdfAOutcome } from '../../../lib/pdf/ops/pdfa';
  import ToolFrame from '../ui/ToolFrame.svelte';

  const LEVELS: { value: PdfALevel; label: string; hint: string }[] = [
    { value: '2b', label: 'PDF/A-2b', hint: '最常用的归档格式，学位论文、档案提交一般要这种。' },
    { value: '3b', label: 'PDF/A-3b', hint: '与 2b 基本相同，对方明确要求 3b 时再选（附件不会保留）。' },
    { value: '1b', label: 'PDF/A-1b', hint: '' },
  ];

  let files = $state<File[]>([]);
  let level = $state<PdfALevel>('2b');
  let keepImages = $state(true);
  let results = $state<(PdfAOutcome & { name: string })[]>([]);
  let failed = $state<FileFailure[]>([]);
  let doneLevel = $state<PdfALevel>('2b');

  const current = $derived(LEVELS.find((l) => l.value === level)!);

  async function run(report: Report) {
    // 运行中改选项不影响这一批
    const list = [...files];
    const chosen = { level, keepImages };
    const { convertFiles } = await import('../../../lib/pdf/ops/pdfa');
    results = [];
    failed = [];
    const { done, failed: errors } = await convertFiles(list, chosen.level, chosen.keepImages, report);
    results = done;
    failed = errors;
    doneLevel = chosen.level;
    return done.map((r) => r.output) satisfies OutputFile[];
  }
</script>

<ToolFrame
  resetKey={files}
  engines={['qpdf', 'ghostscript']}
  runLabel={files.length > 1 ? `转换 ${files.length} 个文件` : `转为 ${current.label}`}
  canRun={files.length > 0}
  blocked="请先选择 PDF"
  zipName="PDFA.zip"
  onrun={run}
  onreset={() => {
    files = [];
    results = [];
    failed = [];
  }}
>
  {#snippet input()}
    <FilePicker bind:files accept="application/pdf,.pdf" multiple maxFiles={20} hint="转换在浏览器里完成，文件不会上传" />
  {/snippet}

  {#snippet options()}
    <div>
      <span class="label">PDF/A 等级</span>
      <Segmented bind:value={level} ariaLabel="PDF/A 等级" options={LEVELS.map(({ value, label }) => ({ value, label }))} />
      {#if level === '1b'}
        <div class="mt-2 flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-[12.5px] leading-relaxed text-warn-ink" role="note">
          <TriangleAlert class="mt-0.5 size-3.5 shrink-0" />
          <span>1b 不支持透明效果：带透明的内容会被转成图片，部分文字无法复制，文件可能大好几倍，耗时也长得多。没有明确要求请选 2b。</span>
        </div>
      {:else}
        <p class="hint">{current.hint}</p>
      {/if}
    </div>
    <Switch bind:checked={keepImages} label="保留图片原画质" description="图片不再重新有损压缩，文件可能变大" />
    <p class="text-[12.5px] leading-snug text-muted">
      嵌入全部字体并统一为 sRGB 色彩；无障碍标签和表单无法保留，所以是 b 级（外观一致）。
    </p>
  {/snippet}

  {#snippet summary()}
    <p>已转为 PDF/A-{doneLevel}{results.length > 1 ? `，共 ${results.length} 个文件` : ''}。</p>
    {#each results as r, i (i)}
      {#if r.warnings.length}
        <div class="mt-2">
          {#if results.length > 1}<p class="mb-1 text-ink">「{r.name}」</p>{/if}
          <ul class="space-y-1">
            {#each r.warnings as warning (warning)}
              <li class="rounded-lg bg-warn-soft px-3 py-1.5 text-[12.5px] text-warn-ink">{warning}</li>
            {/each}
          </ul>
        </div>
      {/if}
    {/each}
    {#if failed.length}
      <p class="mt-3 mb-1.5">{failed.length} 个文件没能转换：</p>
      <ul class="space-y-1">
        {#each failed as f, i (i)}
          <li class="rounded-lg bg-bad-soft px-3 py-1.5 text-[12.5px] text-bad-ink">「{f.name}」{f.message}</li>
        {/each}
      </ul>
    {/if}
  {/snippet}
</ToolFrame>
