<script lang="ts">
  import FilePicker from '../../../components/FilePicker.svelte';
  import { renamed, type Report } from '../../../lib/pdf/files';
  import { readUserPdf } from '../../../lib/pdf/input';
  import { expandRanges, rangeError } from '../../../lib/pdf/ranges';
  import ToolFrame from '../ui/ToolFrame.svelte';

  let files = $state<File[]>([]);
  let pages = $state('');
  let converted = $state(0);
  let warnings = $state<string[]>([]);

  const badRange = $derived(pages.trim() ? rangeError(pages) : '');

  // 三个阶段在总时长里的大致占比（实测 15 页论文：提取版面约 40%、识别段落表格约 12%、写入 Word 约 45%）
  const STAGES: Record<string, [number, number, string]> = {
    extract: [0, 0.42, '分析'],
    parse: [0.42, 0.56, '转换'],
    write: [0.56, 1, '写入'],
  };

  async function run(report: Report, stop: AbortSignal) {
    warnings = [];
    const file = files[0];
    const { pdfToDocx } = await import('../../../lib/pdf/ops/pdf2docx');
    report(null, '读取文件');
    const { bytes, pages: total } = await readUserPdf(file, { countPages: true });
    const indices = pages.trim() ? expandRanges(pages, total) : null;
    report(null, '加载引擎');
    // 点停止或离开页面时 stop 会中止（ToolFrame），转换随之停下，否则回来再转要排在它后面等
    const { blob, meta } = await pdfToDocx(bytes, { pages: indices, signal: stop }, ({ stage, done, total: n }) => {
      const span = STAGES[stage];
      if (!span || !n) return report(null, '加载引擎');
      const [from, to, verb] = span;
      report(from + ((to - from) * (done - 1)) / n, `${verb}第 ${done}/${n} 页`);
    });
    converted = meta.pages - meta.failed.length;
    if (meta.failed.length) warnings.push(`第 ${meta.failed.join('、')} 页转换失败，已跳过`);
    if (!meta.text) warnings.push('没有找到文字层，可能是扫描件，Word 里只有图片。可先用「OCR 文字识别」再转换');
    return [{ name: renamed(file.name, '', 'docx'), blob }];
  }
</script>

<ToolFrame
  resetKey={files}
  runLabel="转为 Word"
  canRun={files.length === 1 && !badRange}
  blocked={badRange ? '页码范围写法有误' : '请选择一个 PDF'}
  onrun={run}
  onreset={() => {
    files = [];
    pages = '';
    warnings = [];
  }}
>
  {#snippet input()}
    <FilePicker bind:files accept="application/pdf,.pdf" hint="加密的 PDF 会先询问密码" />
  {/snippet}
  {#snippet options()}
    <div>
      <label class="label" for="pdf2word-pages">页码范围</label>
      <input
        id="pdf2word-pages"
        class="field font-mono text-[13px]"
        placeholder="全部页"
        bind:value={pages}
        aria-invalid={!!badRange}
        aria-describedby="pdf2word-pages-hint"
      />
      {#if badRange}
        <p id="pdf2word-pages-hint" class="mt-1 text-[12px] text-bad-ink">{badRange}</p>
      {:else}
        <p id="pdf2word-pages-hint" class="hint">例如 1-3,5,8-，留空转换全部页</p>
      {/if}
    </div>
    <p class="text-[12.5px] leading-snug text-muted">多栏排版、公式较多的页面转换后可能需要在 Word 里再调整。</p>
  {/snippet}
  {#snippet summary()}
    <p>已转换 {converted} 页。</p>
    {#if warnings.length}
      <ul class="mt-2 space-y-1">
        {#each warnings as warning (warning)}
          <li class="rounded-lg bg-warn-soft px-3 py-1.5 text-[12.5px] text-warn-ink">{warning}</li>
        {/each}
      </ul>
    {/if}
  {/snippet}
</ToolFrame>
