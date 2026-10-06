<script lang="ts">
  import FilePicker from '../../../components/FilePicker.svelte';
  import Segmented from '../../../components/Segmented.svelte';
  import { LoaderCircle } from '../../../lib/icons';
  import { pdfBlob, renamed, type Report } from '../../../lib/pdf/files';
  import type { Angle, PdfInfo } from '../../../lib/pdf/ops/split';
  import { rangeError } from '../../../lib/pdf/ranges';
  import ToolFrame from '../ui/ToolFrame.svelte';

  type AngleValue = '90' | '180' | '270';

  let files = $state<File[]>([]);
  let angle = $state<AngleValue>('90');
  let spec = $state('');
  let probe = $state<{ file: File; info: PdfInfo | null } | null>(null);
  let wasEncrypted = $state(false);

  const file = $derived(files[0]);
  const info = $derived(probe && probe.file === file ? probe.info : null);
  const probing = $derived(!!file && probe?.file !== file);
  const total = $derived(info?.pages);
  const specError = $derived(spec.trim() ? rangeError(spec, total) : '');
  // 预览图：向左 90° 显示为 -90°，动画方向与实际一致
  const preview = $derived(angle === '270' ? -90 : Number(angle));

  // 选好文件就读一下页数（qpdf 很小，顺便预先下载），用来校验页码
  $effect(() => {
    const current = file;
    if (!current) return;
    let stale = false;
    void import('../../../lib/pdf/ops/split')
      .then(({ inspectPdf }) => inspectPdf(current))
      .catch(() => null)
      .then((result) => {
        if (!stale) probe = { file: current, info: result };
      });
    return () => {
      stale = true;
    };
  });

  async function run(report: Report) {
    const { loadPdf, rotatePdf } = await import('../../../lib/pdf/ops/split');
    report(null, `读取「${file.name}」`);
    const pdf = await loadPdf(file);
    wasEncrypted = pdf.encrypted;
    probe = { file, info: { pages: pdf.pages, locked: false, broken: false } };
    report(null, '正在旋转');
    const out = await rotatePdf(pdf.bytes, pdf.pages, Number(angle) as Angle, spec);
    return [{ name: renamed(file.name, '已旋转'), blob: pdfBlob(out) }];
  }
</script>

<ToolFrame
  resetKey={files}
  runLabel={spec.trim() ? '旋转所选页面' : '旋转全部页面'}
  canRun={!!file && !specError}
  blocked={file ? '页码写法有误，请检查' : '请选择一个 PDF'}
  summary={wasEncrypted ? unlockedNote : undefined}
  onrun={run}
  onreset={() => {
    files = [];
    wasEncrypted = false;
  }}
>
  {#snippet input()}
    <FilePicker bind:files accept="application/pdf,.pdf" hint="页面在原有角度上继续旋转，文字和链接保持不变" />
  {/snippet}

  {#snippet options()}
    <div>
      <span class="label">旋转方向</span>
      <Segmented
        bind:value={angle}
        ariaLabel="旋转方向"
        options={[
          { value: '90', label: '向右 90°' },
          { value: '180', label: '180°' },
          { value: '270', label: '向左 90°' },
        ]}
      />
      <!-- 示意：虚线框是原来的朝向 -->
      <div class="mt-3 grid h-20 place-items-center rounded-xl bg-surface-2/60" aria-hidden="true">
        <div class="h-12 w-9 rounded-[3px] border border-dashed border-line-strong [grid-area:1/1]"></div>
        <div
          class="flex h-12 w-9 flex-col gap-[3px] rounded-[3px] border border-line-strong bg-surface p-1.5 shadow-card transition-transform duration-300 [grid-area:1/1]"
          style:transform="rotate({preview}deg)"
        >
          <span class="h-[3px] w-3.5 rounded-full bg-accent"></span>
          <span class="h-[2px] w-full rounded-full bg-line-strong"></span>
          <span class="h-[2px] w-full rounded-full bg-line-strong"></span>
          <span class="h-[2px] w-3/4 rounded-full bg-line-strong"></span>
        </div>
      </div>
    </div>

    <div>
      <label class="label" for="rotate-spec">页码范围 <span class="font-normal text-muted">（可选）</span></label>
      <input
        id="rotate-spec"
        class="field font-mono text-[13px]"
        placeholder="全部页"
        autocomplete="off"
        bind:value={spec}
        aria-invalid={!!specError}
        aria-describedby="rotate-spec-note"
      />
      <p id="rotate-spec-note" class={specError ? 'mt-1 text-[12px] text-bad-ink' : 'hint'}>
        {#if specError}
          {specError}
        {:else if probing}
          <span class="inline-flex items-center gap-1"><LoaderCircle class="size-3 animate-spin" />正在读取页数…</span>
        {:else if info?.locked}
          文件有打开密码，开始处理时会询问；例如 1-3,5
        {:else if info?.broken}
          <span class="text-warn-ink">读不出页数，文件可能已损坏</span>
        {:else if total}
          共 {total} 页，例如 1-3,5；留空旋转全部页
        {:else}
          例如 1-3,5；留空旋转全部页
        {/if}
      </p>
    </div>
  {/snippet}
</ToolFrame>

{#snippet unlockedNote()}原文件带有密码或权限限制，导出的文件已不再加密。{/snippet}
