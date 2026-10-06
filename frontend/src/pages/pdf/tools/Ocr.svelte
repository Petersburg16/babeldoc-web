<script lang="ts">
  import FilePicker from '../../../components/FilePicker.svelte';
  import Segmented from '../../../components/Segmented.svelte';
  import Switch from '../../../components/Switch.svelte';
  import { copyText, duration } from '../../../lib/format';
  import { Copy, Download } from '../../../lib/icons';
  import { download, pdfBlob, readBytes, renamed, type Report } from '../../../lib/pdf/files';
  import type { OcrLanguage, OcrResult } from '../../../lib/pdf/ops/ocr';
  import { rangeError } from '../../../lib/pdf/ranges';
  import { toast } from '../../../lib/toast.svelte';
  import ToolFrame from '../ui/ToolFrame.svelte';

  let files = $state<File[]>([]);
  let language = $state<OcrLanguage>('chi_sim+eng');
  let dpi = $state<'200' | '300'>('300');
  let skipText = $state(true);
  let range = $state('');
  let done = $state.raw<(Omit<OcrResult, 'pdf'> & { name: string; seconds: number; encrypted: boolean; dpi: string }) | null>(null);

  const badRange = $derived(range.trim() ? rangeError(range) : '');

  // 离开页面时停掉还在跑的识别，释放 worker 占的内存
  let controller: AbortController | null = null;
  $effect(() => () => controller?.abort());

  async function run(report: Report, stop: AbortSignal) {
    const [{ ocrPdf }, { unlockPdf }, { QpdfError }] = await Promise.all([
      import('../../../lib/pdf/ops/ocr'),
      import('../../../lib/pdf/input'),
      import('../../../lib/pdf/engines/qpdf'),
    ]);
    const file = files[0];
    done = null;
    report(null, `读取「${file.name}」`);
    const bytes = await readBytes(file);
    let unlocked;
    try {
      unlocked = await unlockPdf(file, bytes);
    } catch (e) {
      if (e instanceof QpdfError) throw new Error(`无法读取「${file.name}」：文件可能已损坏，或不是 PDF`);
      throw e;
    }
    controller = new AbortController();
    const current = controller;
    stop.addEventListener('abort', () => current.abort(), { once: true });
    const started = performance.now();
    try {
      const { pdf, ...rest } = await ocrPdf(unlocked.bytes, {
        language,
        dpi: Number(dpi),
        range: range.trim(),
        skipText,
        signal: controller.signal,
        report,
      });
      done = { ...rest, name: file.name, seconds: (performance.now() - started) / 1000, encrypted: unlocked.encrypted, dpi };
      return [{ name: renamed(file.name, 'OCR'), blob: pdfBlob(pdf) }];
    } finally {
      controller = null;
    }
  }

  async function copy() {
    if (done && (await copyText(done.text))) toast.success('已复制识别出的文字');
    else toast.error('复制失败，请改用下载文本');
  }

  function saveText() {
    if (!done) return;
    download({ name: renamed(done.name, 'OCR', 'txt'), blob: new Blob([done.text], { type: 'text/plain;charset=utf-8' }) });
  }
</script>

<ToolFrame
  resetKey={files}
  runLabel="开始识别"
  canRun={files.length === 1 && !badRange}
  blocked={badRange ? '页码范围写法有误' : '选择一个扫描版 PDF'}
  onrun={run}
  onreset={() => {
    files = [];
    range = '';
    done = null;
  }}
>
  {#snippet input()}
    <FilePicker bind:files accept="application/pdf,.pdf" hint="扫描件或拍照生成的 PDF；页面横着或倒着的，先用「旋转 PDF」转正" />
  {/snippet}
  {#snippet options()}
    <div>
      <span class="label">识别语言</span>
      <Segmented
        bind:value={language}
        ariaLabel="识别语言"
        options={[
          { value: 'chi_sim+eng', label: '中文 + 英文' },
          { value: 'eng', label: '仅英文' },
        ]}
      />
    </div>
    <div>
      <span class="label">识别分辨率</span>
      <Segmented
        bind:value={dpi}
        ariaLabel="识别分辨率"
        options={[
          { value: '200', label: '200 dpi' },
          { value: '300', label: '300 dpi' },
        ]}
      />
      <p class="hint">300 dpi 更准，字小的扫描件建议用；200 dpi 更快</p>
    </div>
    <div>
      <label class="label" for="ocr-range">页码范围</label>
      <input id="ocr-range" class="field font-mono text-[13px]" placeholder="全部页" bind:value={range} aria-invalid={!!badRange} />
      {#if badRange}
        <p class="mt-1 text-[12px] text-bad-ink">{badRange}</p>
      {:else}
        <p class="hint">例如 1-3,5,8-；每页约需数秒</p>
      {/if}
    </div>
    <Switch bind:checked={skipText} label="跳过已有文字的页" description="这些页本来就能搜索，再识别会叠出两层文字" />
  {/snippet}
  {#snippet summary()}
    {#if done}
      <p>
        识别了 {done.recognized} 页{done.skipped ? `，跳过 ${done.skipped} 页已有文字的页` : ''}，用时 {duration(done.seconds)}。{done.encrypted
          ? '原文件的密码已去掉，生成的 PDF 不再加密。'
          : ''}
      </p>
      {#if done.doubtful.length}
        <p class="mt-1 text-warn-ink">
          第 {done.doubtful.join('、')} 页识别结果可能不准：页面可能横着、倒着或不够清晰，转正后再识别效果更好。
        </p>
      {/if}
      {#if done.failed.length}
        <p class="mt-1 text-warn-ink">
          {done.failed.map((f) => `第 ${f.page} 页`).join('、')}识别失败（{done.failed[0].reason}），已保持原样。
        </p>
      {/if}
      {#if !done.empty}
        <div class="mt-2.5 flex flex-wrap gap-2">
          <button class="btn btn-secondary btn-sm" onclick={copy}><Copy class="size-3.5" /> 复制文字</button>
          <button class="btn btn-secondary btn-sm" onclick={saveText}><Download class="size-3.5" /> 下载文本（.txt）</button>
        </div>
        <details class="mt-2.5">
          <summary class="cursor-pointer text-[12.5px] text-muted select-none hover:text-ink">预览识别出的文字</summary>
          <pre
            class="mt-2 max-h-64 overflow-auto rounded-lg border border-line bg-surface-2/40 px-3 py-2.5 font-sans text-[12.5px] leading-relaxed whitespace-pre-wrap text-ink">{done.text}</pre>
        </details>
      {:else}
        <p class="mt-1 text-muted">没有识别出文字，请确认页面上有清晰的文字{done.dpi === '200' ? '，或改用 300 dpi 再试' : ''}。</p>
      {/if}
    {/if}
  {/snippet}
</ToolFrame>
