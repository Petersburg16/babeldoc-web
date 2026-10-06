<script lang="ts">
  import FilePicker from '../../../components/FilePicker.svelte';
  import Segmented from '../../../components/Segmented.svelte';
  import { LoaderCircle } from '../../../lib/icons';
  import { stem, type Report } from '../../../lib/pdf/files';
  import { readUserPdf } from '../../../lib/pdf/input';
  import type { SplitMode } from '../../../lib/pdf/ops/split';
  import { PdfProbe } from '../../../lib/pdf/probe.svelte';
  import { parseRanges, rangeError } from '../../../lib/pdf/ranges';
  import PageRangeField from '../ui/PageRangeField.svelte';
  import ToolFrame from '../ui/ToolFrame.svelte';

  const MODES: { value: SplitMode; label: string; hint: string }[] = [
    { value: 'ranges', label: '按范围', hint: '逗号分隔的每一段各存成一个文件' },
    { value: 'every', label: '每 N 页', hint: '从第 1 页起每 N 页存成一个文件，不带书签和文档属性' },
    { value: 'single', label: '逐页', hint: '每一页单独存成一个文件，不带书签和文档属性' },
    { value: 'extract', label: '提取页面', hint: '把所选页按填写顺序合成一个新 PDF' },
  ];

  let files = $state<File[]>([]);
  let mode = $state<SplitMode>('ranges');
  let spec = $state('');
  let every = $state<number | null>(2);
  let wasEncrypted = $state(false);

  const file = $derived(files[0]);
  // 选好文件就读一下页数，用来校验页码、预告会生成几个文件
  const probe = new PdfProbe(() => file);
  const total = $derived(probe.total);
  const needsSpec = $derived(mode === 'ranges' || mode === 'extract');
  const specError = $derived(needsSpec && spec.trim() ? rangeError(spec, total) : '');
  const everyOk = $derived(typeof every === 'number' && Number.isInteger(every) && every >= 1);
  /** 预计生成几个文件；页数未知或写法有误时为 0 */
  const planned = $derived.by(() => {
    if (!total) return 0;
    if (mode === 'single') return total;
    if (mode === 'every') return everyOk ? Math.ceil(total / every!) : 0;
    if (!spec.trim() || specError) return 0;
    return mode === 'extract' ? 1 : parseRanges(spec, total).length;
  });
  const ready = $derived(needsSpec ? !!spec.trim() && !specError : mode === 'every' ? everyOk : true);

  async function run(report: Report) {
    const { splitPdf } = await import('../../../lib/pdf/ops/split');
    report(null, `读取「${file.name}」`);
    const pdf = await readUserPdf(file, { countPages: true });
    wasEncrypted = pdf.encrypted;
    probe.learned(file, pdf.pages);
    return splitPdf(file.name, pdf.bytes, pdf.pages, { mode, spec, every: every ?? 1 }, report);
  }
</script>

<ToolFrame
  resetKey={files}
  runLabel={mode === 'extract' ? '提取页面' : planned > 1 ? `拆分为 ${planned} 个文件` : '开始拆分'}
  canRun={!!file && ready}
  blocked={!file
    ? '请选择一个 PDF'
    : specError
      ? '页码写法有误，请检查'
      : needsSpec
        ? '请填写页码'
        : '每份页数应为正整数'}
  zipName={file ? `${stem(file.name)}-拆分.zip` : undefined}
  summary={wasEncrypted ? unlockedNote : undefined}
  onrun={run}
  onreset={() => {
    files = [];
    wasEncrypted = false;
  }}
>
  {#snippet input()}
    <FilePicker bind:files accept="application/pdf,.pdf" hint="一次拆分一个文件，结果可逐个下载或打包下载" />
  {/snippet}

  {#snippet options()}
    <div>
      <span class="label">拆分方式</span>
      <Segmented bind:value={mode} ariaLabel="拆分方式" options={MODES} />
      <p class="hint">{MODES.find((m) => m.value === mode)?.hint}</p>
    </div>

    {#if needsSpec}
      <PageRangeField
        id="split-spec"
        label={mode === 'extract' ? '要提取的页' : '页码范围'}
        placeholder={mode === 'extract' ? '如 1,3,5-8' : '如 1-3,4-6,7-'}
        bind:value={spec}
        error={specError}
        hint="“7-”表示第 7 页到末页，“-3”表示前 3 页"
      />
    {:else if mode === 'every'}
      <div>
        <label class="label" for="split-every">每份页数</label>
        <input id="split-every" class="field" type="number" min="1" step="1" inputmode="numeric" bind:value={every} />
      </div>
    {/if}

    {#if file}
      <div class="rounded-lg bg-surface-2 px-3 py-2 text-[12.5px] leading-relaxed text-ink-2" aria-live="polite">
        {#if probe.probing}
          <span class="flex items-center gap-1.5 text-muted"><LoaderCircle class="size-3.5 animate-spin" />正在读取页数…</span>
        {:else if probe.info?.locked}
          文件有打开密码，开始处理时会询问
        {:else if probe.info?.broken}
          <span class="text-warn-ink">读不出页数，文件可能已损坏</span>
        {:else if total}
          共 {total} 页{#if planned}，将生成 {planned} 个文件{/if}
        {:else}
          页数会在开始处理时读取
        {/if}
      </div>
    {/if}
  {/snippet}
</ToolFrame>

{#snippet unlockedNote()}原文件带有密码或权限限制，拆分出的文件已不再加密。{/snippet}
