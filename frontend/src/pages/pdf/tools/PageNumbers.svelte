<script lang="ts">
  import FilePicker from '../../../components/FilePicker.svelte';
  import { bytes } from '../../../lib/format';
  import { type EngineId, engines } from '../../../lib/pdf/engines.svelte';
  import { CJK_FONTS, type CjkFont } from '../../../lib/pdf/engines/pdflib';
  import { pdfBlob, renamed, type Report } from '../../../lib/pdf/files';
  import type { Align, NumberFormat, Vertical } from '../../../lib/pdf/ops/pagenumbers';
  import { rangeError } from '../../../lib/pdf/ranges';
  import ToolFrame from '../ui/ToolFrame.svelte';

  const FORMATS: { id: NumberFormat; label: (n: number) => string; zh?: boolean }[] = [
    { id: 'n', label: (n) => `${n}` },
    { id: 'n-of-total', label: (n) => `${n} / N` },
    { id: 'dash', label: (n) => `- ${n} -` },
    { id: 'zh', label: (n) => `第 ${n} 页`, zh: true },
    { id: 'zh-of-total', label: (n) => `第 ${n} 页 / 共 N 页`, zh: true },
  ];

  const POSITIONS: { v: Vertical; a: Align; label: string }[] = [
    { v: 'top', a: 'left', label: '页眉靠左' },
    { v: 'top', a: 'center', label: '页眉居中' },
    { v: 'top', a: 'right', label: '页眉靠右' },
    { v: 'bottom', a: 'left', label: '页脚靠左' },
    { v: 'bottom', a: 'center', label: '页脚居中' },
    { v: 'bottom', a: 'right', label: '页脚靠右' },
  ];

  let files = $state<File[]>([]);
  let format = $state<NumberFormat>('n');
  let vertical = $state<Vertical>('bottom');
  let align = $state<Align>('center');
  let start = $state(1);
  let fontSize = $state(10);
  let marginMm = $state(10);
  let pages = $state('');
  let font = $state<CjkFont>('font-sans');
  let count = $state(0);
  let standard = $state('');

  const current = $derived(FORMATS.find((f) => f.id === format) ?? FORMATS[0]);
  const zh = $derived(!!current.zh);
  const position = $derived(POSITIONS.find((p) => p.v === vertical && p.a === align) ?? POSITIONS[4]);
  const fontMeta = $derived(CJK_FONTS.find((f) => f.id === font) ?? CJK_FONTS[0]);
  const fontPending = $derived(zh ? engines.pendingBytes([font as EngineId]) : 0);

  const rangeErr = $derived(pages.trim() ? rangeError(pages) : '');
  const problem = $derived(
    !Number.isInteger(start) || start < 0 || start > 99999
      ? '起始页码需是 0–99999 的整数'
      : !Number.isFinite(fontSize) || fontSize < 4 || fontSize > 72
        ? '字号需在 4–72 之间'
        : !Number.isFinite(marginMm) || marginMm < 0 || marginMm > 100
          ? '边距需在 0–100 mm 之间'
          : rangeErr
            ? '要加页码的页写法有误'
            : '',
  );

  async function run(report: Report) {
    const [{ unlockPdf }, { QpdfError }, { addPageNumbers }] = await Promise.all([
      import('../../../lib/pdf/input'),
      import('../../../lib/pdf/engines/qpdf'),
      import('../../../lib/pdf/ops/pagenumbers'),
    ]);
    const file = files[0];
    if (!file.size) throw new Error(`「${file.name}」是空文件，请重新选择`);
    report(null, `读取「${file.name}」`);
    let input: Uint8Array;
    try {
      ({ bytes: input } = await unlockPdf(file));
    } catch (e) {
      // qpdf 的原始报错带着 worker 里的临时文件名（/in.pdf: …），给用户看时去掉
      if (!(e instanceof QpdfError)) throw e;
      const detail = e.message.replace(/^PDF 处理失败：/, '').replace(/\/[^\s:/]+:\s*/g, '');
      throw new Error(`「${file.name}」读取失败，文件可能已损坏或不是有效的 PDF${detail ? `（${detail}）` : ''}`);
    }
    const out = await addPageNumbers(
      input,
      { format, vertical, align, start, pages, fontSize, margin: (marginMm * 72) / 25.4, font },
      report,
    );
    count = out.count;
    standard = out.standard;
    return [{ name: renamed(file.name, '页码'), blob: pdfBlob(out.bytes) }];
  }
</script>

<ToolFrame
  resetKey={files}
  runLabel="添加页码"
  canRun={files.length === 1 && !problem}
  blocked={files.length ? problem : '先选择一个 PDF'}
  onrun={run}
  onreset={() => (files = [])}
>
  {#snippet input()}
    <FilePicker bind:files accept="application/pdf,.pdf" hint="页码以文字写入，可搜索、可复制；横向和旋转过的页面会自动摆正" />
  {/snippet}

  {#snippet options()}
    <div>
      <span class="label">格式</span>
      <div class="grid grid-cols-6 gap-1.5" role="radiogroup" aria-label="页码格式">
        {#each FORMATS as f (f.id)}
          <button
            type="button"
            role="radio"
            aria-checked={format === f.id}
            class="h-9 rounded-[9px] border px-2 text-[13px] whitespace-nowrap tabular transition-colors
              {f.zh ? 'col-span-3' : 'col-span-2'}
              {format === f.id
              ? 'border-accent bg-accent-soft font-medium text-accent-ink'
              : 'border-line-strong bg-surface text-ink-2 hover:border-accent/60 hover:text-ink'}"
            onclick={() => (format = f.id)}
          >
            {f.label(1)}
          </button>
        {/each}
      </div>
    </div>

    <div>
      <div class="mb-[0.35rem] flex items-baseline justify-between">
        <span class="label mb-0">位置</span>
        <span class="text-[12px] text-muted">{position.label}</span>
      </div>
      <div class="flex items-start gap-4">
        <!-- 小页面示意：点六个位置之一；选中处只显示数字，长格式整段放进去会盖住同一行的其他位置 -->
        <div
          class="relative h-[212px] w-[150px] shrink-0 rounded-md border border-line-strong bg-surface shadow-card"
          role="radiogroup"
          aria-label="页码位置"
        >
          <div class="absolute inset-x-5 top-10 bottom-10 flex flex-col gap-[7px]" aria-hidden="true">
            {#each [100, 94, 98, 62, 0, 100, 90, 96, 100, 74, 0, 96, 88, 54] as w, i (i)}
              <span class="h-[3px] shrink-0 rounded-full {w ? 'bg-line' : ''}" style="width: {w}%"></span>
            {/each}
          </div>
          {#each POSITIONS as p (p.label)}
            {@const on = p.v === vertical && p.a === align}
            <button
              type="button"
              role="radio"
              aria-checked={on}
              aria-label={p.label}
              title={p.label}
              class="absolute flex h-5 w-9 items-center justify-center rounded text-[10px] leading-none tabular transition-colors
                {p.v === 'top' ? 'top-2' : 'bottom-2'}
                {p.a === 'left' ? 'left-2' : p.a === 'right' ? 'right-2' : 'left-1/2 -translate-x-1/2'}
                {on
                ? 'bg-accent font-semibold text-white'
                : 'border border-dashed border-line-strong hover:border-accent hover:bg-accent-soft/60'}"
              onclick={() => {
                vertical = p.v;
                align = p.a;
              }}
            >
              {#if on}{Number.isInteger(start) ? start : 1}{/if}
            </button>
          {/each}
        </div>
        <div class="min-w-0 flex-1 space-y-2.5">
          <div>
            <label class="label" for="pn-start">起始页码</label>
            <input id="pn-start" class="field tabular" type="number" min="0" max="99999" step="1" bind:value={start} />
          </div>
          <div>
            <label class="label" for="pn-size">字号</label>
            <input id="pn-size" class="field tabular" type="number" min="4" max="72" step="0.5" bind:value={fontSize} />
          </div>
          <div>
            <label class="label" for="pn-margin">边距（mm）</label>
            <input id="pn-margin" class="field tabular" type="number" min="0" max="100" step="1" bind:value={marginMm} />
          </div>
        </div>
      </div>
    </div>

    <div>
      <label class="label" for="pn-pages">加页码的页 <span class="font-normal text-muted">（可选）</span></label>
      <input
        id="pn-pages"
        class="field font-mono text-[13px]"
        placeholder="全部页"
        bind:value={pages}
        aria-invalid={!!rangeErr}
      />
      {#if rangeErr}
        <p class="mt-1 text-[12px] text-bad-ink">{rangeErr}</p>
      {:else}
        <p class="hint">例如填 2- 跳过封面，编号仍从起始页码开始</p>
      {/if}
    </div>

    {#if zh}
      <div>
        <label class="label" for="pn-font">中文字体</label>
        <select id="pn-font" class="field" bind:value={font}>
          {#each CJK_FONTS as f (f.id)}
            <option value={f.id}>{f.label}</option>
          {/each}
        </select>
        {#if fontPending > 0}
          <p class="hint">首次使用需下载{fontMeta.label}，约 {bytes(fontPending)}，之后浏览器会缓存</p>
        {/if}
      </div>
    {/if}
  {/snippet}

  {#snippet summary()}
    已给 {count} 页加上页码。{standard ? `原文件声明为 ${standard}，页码字体已嵌入文件。` : ''}
  {/snippet}
</ToolFrame>
