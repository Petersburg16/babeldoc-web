<script lang="ts">
  import FilePicker from '../../../components/FilePicker.svelte';
  import Segmented from '../../../components/Segmented.svelte';
  import { readBytes, stem, type Report } from '../../../lib/pdf/files';
  import type { ImageFormat } from '../../../lib/pdf/ops/images';
  import { rangeError } from '../../../lib/pdf/ranges';
  import ToolFrame from '../ui/ToolFrame.svelte';

  let files = $state<File[]>([]);
  let format = $state<ImageFormat>('png');
  let quality = $state(90);
  let dpi = $state<'72' | '150' | '300'>('150');
  let pages = $state('');
  let zipName = $state<string>();
  let info = $state<{ format: ImageFormat; dpi: string; sizes: [number, number][]; reduced: number[] } | null>(null);

  const error = $derived(pages.trim() ? rangeError(pages) : '');
  const formatName = $derived(format === 'png' ? 'PNG' : 'JPG');
  const dpiHint = { '72': '适合预览和缩略图，文件最小', '150': '适合屏幕阅读和分享', '300': '适合打印，文件较大' };

  async function run(report: Report) {
    const [{ unlockPdf }, { QpdfError }, { pdfToImages }] = await Promise.all([
      import('../../../lib/pdf/input'),
      import('../../../lib/pdf/engines/qpdf'),
      import('../../../lib/pdf/ops/images'),
    ]);
    const file = files[0];
    const chosen = { format, dpi };
    const opts = { format, dpi: Number(dpi), quality: quality / 100, pages };
    report(null, `读取「${file.name}」`);
    let bytes: Uint8Array;
    try {
      ({ bytes } = await unlockPdf(file, await readBytes(file)));
    } catch (e) {
      // qpdf 的报错是英文术语（如 can't find startxref），换成用户看得懂的说法
      if (e instanceof QpdfError) throw new Error(`无法读取「${file.name}」，文件可能已损坏或不是 PDF`, { cause: e });
      throw e;
    }
    const result = await pdfToImages(bytes, file.name, opts, report);
    zipName = `${stem(file.name)}-图片.zip`;
    info = { ...chosen, sizes: result.sizes, reduced: result.reduced };
    return result.outputs;
  }

  function sizeText(sizes: [number, number][]) {
    const [w, h] = sizes[0];
    return sizes.every(([a, b]) => a === w && b === h) ? `${w} × ${h} 像素` : '尺寸随页面大小不同';
  }
</script>

<ToolFrame
  resetKey={files}
  engines={['qpdf', 'render']}
  runLabel="转为 {formatName}"
  canRun={files.length === 1 && !error}
  blocked={error ? '页码范围写法有误' : '请先选择一个 PDF'}
  onrun={run}
  {zipName}
  onreset={() => {
    files = [];
    pages = '';
    info = null;
  }}
>
  {#snippet input()}
    <FilePicker bind:files accept="application/pdf,.pdf" hint="每页导出为一张图片，多张时可打包下载" />
  {/snippet}
  {#snippet options()}
    <div>
      <span class="label">图片格式</span>
      <Segmented
        bind:value={format}
        ariaLabel="图片格式"
        options={[
          { value: 'png', label: 'PNG' },
          { value: 'jpeg', label: 'JPG' },
        ]}
      />
      <p class="hint">{format === 'png' ? '无损，适合文字和线条' : '体积小，适合照片和扫描件'}</p>
    </div>
    {#if format === 'jpeg'}
      <div>
        <label class="label flex items-center justify-between" for="img-quality">
          图片质量 <span class="font-normal text-muted tabular">{quality}%</span>
        </label>
        <input id="img-quality" class="w-full accent-accent" type="range" min="50" max="100" step="5" bind:value={quality} />
      </div>
    {/if}
    <div>
      <span class="label">分辨率</span>
      <Segmented
        bind:value={dpi}
        ariaLabel="分辨率"
        options={[
          { value: '72', label: '72 dpi' },
          { value: '150', label: '150 dpi' },
          { value: '300', label: '300 dpi' },
        ]}
      />
      <p class="hint">{dpiHint[dpi]}</p>
    </div>
    <div>
      <label class="label" for="img-pages">页码范围 <span class="font-normal text-muted">（可选）</span></label>
      <input id="img-pages" class="field font-mono text-[13px] placeholder:font-sans" placeholder="全部页，或如 1-3,5,8-" bind:value={pages} aria-invalid={!!error} />
      {#if error}<p class="mt-1 text-[12px] text-bad-ink">{error}</p>{/if}
    </div>
  {/snippet}
  {#snippet summary(outputs)}
    {#if info}
      <p>
        共 {outputs.length} 张 {info.format === 'png' ? 'PNG' : 'JPG'}，{info.dpi} dpi，{sizeText(info.sizes)}
      </p>
      {#if info.reduced.length}
        <p class="mt-1 text-warn-ink">
          {info.reduced.length > 6 ? `有 ${info.reduced.length} 页` : `第 ${info.reduced.join('、')} 页`}尺寸过大，已按浏览器的画布上限降低分辨率
        </p>
      {/if}
    {/if}
  {/snippet}
</ToolFrame>
