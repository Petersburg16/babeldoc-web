<script lang="ts">
  import FilePicker from '../../../components/FilePicker.svelte';
  import Segmented from '../../../components/Segmented.svelte';
  import { bytes } from '../../../lib/format';
  import { Info, TriangleAlert, X } from '../../../lib/icons';
  import { type EngineId, engines } from '../../../lib/pdf/engines.svelte';
  import { CJK_FONTS, type CjkFont, needsCjkFont } from '../../../lib/pdf/engines/pdflib';
  import { pdfBlob, readBytes, renamed, type Report } from '../../../lib/pdf/files';
  import { CloudDownload, ImagePlus } from '../../../lib/pdf/icons';
  import { readUserPdf } from '../../../lib/pdf/input';
  import type { Watermark } from '../../../lib/pdf/ops/watermark';
  import { rangeError } from '../../../lib/pdf/ranges';
  import ToolFrame from '../ui/ToolFrame.svelte';

  type Kind = 'text' | 'image';
  type Angle = '0' | '30' | '45' | '90';

  const COLORS = [
    { value: '#8c8c8c', label: '灰色' },
    { value: '#d03b3b', label: '红色' },
    { value: '#2a78d6', label: '蓝色' },
    { value: '#1f1f1f', label: '黑色' },
  ];
  const ANGLES: { value: Angle; label: string }[] = ['0', '30', '45', '90'].map((a) => ({ value: a as Angle, label: `${a}°` }));

  let files = $state<File[]>([]);
  let kind = $state<Kind>('text');
  let text = $state('');
  let font = $state<CjkFont>('font-sans');
  let size = $state<number | null>(48);
  let color = $state(COLORS[0].value);
  let opacity = $state(30);
  // 文字默认斜放，图片（多为标志）默认正放
  let angles = $state<Record<Kind, Angle>>({ text: '45', image: '0' });
  let layout = $state<'center' | 'tile'>('center');
  let pages = $state('');
  let image = $state<File | null>(null);
  let imageWidth = $state(40);

  // 结果的补充信息
  let missing = $state<string[]>([]);
  let wasEncrypted = $state(false);
  let preview = $state<{ url: string; page: number } | null>(null);
  let runs = 0;

  const customColor = $derived(!COLORS.some((c) => c.value === color));
  const needsFont = $derived(kind === 'text' && needsCjkFont(text));
  // 工具栏的下载提示不含字体，这里单独提示
  const fontPending = $derived(needsFont ? engines.pendingBytes([font as EngineId]) : 0);
  const pagesError = $derived(pages.trim() ? rangeError(pages) : '');
  const sizeOk = $derived(typeof size === 'number' && size >= 6 && size <= 400);
  const problem = $derived(
    !files.length
      ? '请选择一个 PDF'
      : kind === 'text' && !text.trim()
        ? '请输入水印文字'
        : kind === 'text' && !sizeOk
          ? '字号请填 6–400 之间的数字'
          : kind === 'image' && !image
            ? '请选择水印图片'
            : pagesError
              ? '页码范围写法有误'
              : '',
  );

  const imageUrl = $derived(image ? URL.createObjectURL(image) : '');
  $effect(() => {
    const url = imageUrl;
    return () => url && URL.revokeObjectURL(url);
  });
  $effect(() => {
    const url = preview?.url;
    return () => url && URL.revokeObjectURL(url);
  });

  function clearResult() {
    runs += 1;
    preview = null;
    missing = [];
    wasEncrypted = false;
  }

  async function run(report: Report) {
    clearResult();
    const token = runs;
    const { addWatermark } = await import('../../../lib/pdf/ops/watermark');
    const file = files[0];
    report(null, `读取「${file.name}」`);
    const unlocked = await readUserPdf(file);
    const common = { pages, opacity: opacity / 100, angle: Number(angles[kind]), tile: layout === 'tile' };
    const mark: Watermark =
      kind === 'text'
        ? { ...common, kind: 'text', text, font, size: size ?? 48, color }
        : { ...common, kind: 'image', image: await readBytes(image!), width: imageWidth / 100 };
    report(null, '正在添加水印');
    const result = await addWatermark(unlocked.bytes, mark, {
      onFont: (p) => {
        // 字体已缓存时也会回调一次（loaded = total），这时不必显示下载
        if (p.loaded < p.total) report(p.loaded / p.total, `正在下载字体（${p.label}）`);
      },
      onPage: (done, total) => (done < total ? report(done / total, '正在添加水印') : report(null, '正在保存')),
    });
    missing = result.missing;
    wasEncrypted = unlocked.encrypted;
    void renderPreview(result.bytes, result.pages[0], token);
    return [{ name: renamed(file.name, '水印'), blob: pdfBlob(result.bytes) }];
  }

  /** 结果出来后再画第一张加了水印的页，不耽误下载 */
  async function renderPreview(data: Uint8Array, index: number, token: number) {
    try {
      const { openPdf, closePdf, renderPage, canvasToBlob, releaseCanvas } = await import('../../../lib/pdf/engines/pdfjs');
      const doc = await openPdf(data);
      try {
        const canvas = await renderPage(doc, index, { width: 800 });
        const blob = await canvasToBlob(canvas, 'image/png');
        releaseCanvas(canvas);
        if (token === runs) preview = { url: URL.createObjectURL(blob), page: index + 1 };
      } finally {
        await closePdf(doc);
      }
    } catch (e) {
      console.warn('水印预览生成失败', e);
    }
  }
</script>

<ToolFrame
  resetKey={files}
  runLabel="添加水印"
  canRun={!problem}
  blocked={problem}
  onrun={run}
  zipName="水印.zip"
  onreset={() => {
    files = [];
    clearResult();
  }}
>
  {#snippet input()}
    <FilePicker bind:files accept="application/pdf,.pdf" hint="加密的 PDF 会先请你输入密码" />
  {/snippet}

  {#snippet options()}
    <Segmented bind:value={kind} ariaLabel="水印类型" options={[{ value: 'text', label: '文字水印' }, { value: 'image', label: '图片水印' }]} />

    {#if kind === 'text'}
      <div>
        <label class="label" for="wm-text">水印文字</label>
        <textarea id="wm-text" class="field" rows="2" maxlength="200" placeholder="例如：内部资料 请勿外传" bind:value={text}></textarea>
      </div>
      <div class="grid grid-cols-[minmax(0,1fr)_92px] gap-2.5">
        <div>
          <label class="label" for="wm-font">中文字体</label>
          <select id="wm-font" class="field" bind:value={font}>
            {#each CJK_FONTS as f (f.id)}<option value={f.id}>{f.label}</option>{/each}
          </select>
        </div>
        <div>
          <label class="label" for="wm-size">字号</label>
          <input id="wm-size" class="field tabular" type="number" min="6" max="400" bind:value={size} aria-invalid={!sizeOk} />
        </div>
      </div>
      {#if fontPending > 0}
        <p class="-mt-1.5 flex items-start gap-1.5 text-[12.5px] leading-snug text-muted">
          <CloudDownload class="mt-px size-3.5 shrink-0" />
          中文水印首次使用需下载字体，约 {bytes(fontPending)}，之后浏览器会缓存
        </p>
      {/if}
      <div>
        <span class="label" id="wm-color-label">颜色</span>
        <div class="flex items-center gap-2.5" role="radiogroup" aria-labelledby="wm-color-label">
          {#each COLORS as c (c.value)}
            <button
              type="button"
              role="radio"
              aria-checked={color === c.value}
              aria-label={c.label}
              title={c.label}
              class="size-7 rounded-full ring-offset-2 ring-offset-surface transition-shadow focus-visible:outline-offset-4
                {color === c.value ? 'ring-2 ring-accent' : 'ring-1 ring-line-strong hover:ring-2'}"
              style:background={c.value}
              onclick={() => (color = c.value)}
            ></button>
          {/each}
          <!-- 取色框本身是透明的，键盘聚焦时把焦点框画在外层；焦点框放在选中圈之外，选中时也能看出聚焦 -->
          <label
            class="relative size-7 cursor-pointer rounded-full ring-offset-2 ring-offset-surface transition-shadow
              has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-4 has-[:focus-visible]:outline-accent
              {customColor ? 'ring-2 ring-accent' : 'ring-1 ring-line-strong hover:ring-2'}"
            style:background={customColor ? color : 'conic-gradient(#e5484d, #f5a524, #46a758, #3e9ef5, #8e4ec6, #e5484d)'}
            title="自定义颜色"
          >
            <input type="color" class="absolute inset-0 size-full cursor-pointer opacity-0" bind:value={color} aria-label="自定义颜色" />
          </label>
          <span class="ml-auto font-mono text-[12px] text-muted uppercase">{color}</span>
        </div>
      </div>
    {:else}
      <div>
        <span class="label">水印图片</span>
        {#if image}
          <div class="flex items-center gap-2.5 rounded-xl border border-line px-2.5 py-2">
            <img src={imageUrl} alt="" class="size-10 shrink-0 rounded-md bg-surface-2 object-contain" />
            <div class="min-w-0 flex-1">
              <p class="truncate text-[13px] font-medium" title={image.name}>{image.name}</p>
              <p class="text-[12px] text-muted">{bytes(image.size)}</p>
            </div>
            <button class="btn btn-ghost btn-sm btn-icon" aria-label="移除图片" onclick={() => (image = null)}><X class="size-4" /></button>
          </div>
        {:else}
          <label class="btn btn-secondary btn-sm cursor-pointer">
            <ImagePlus class="size-3.5" /> 选择图片
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
              class="hidden"
              onchange={(e) => {
                image = e.currentTarget.files?.[0] ?? null;
                e.currentTarget.value = '';
              }}
            />
          </label>
          <p class="hint">PNG、JPG 或 WebP，透明背景的 PNG 效果最好</p>
        {/if}
      </div>
      <div>
        <div class="flex items-baseline justify-between">
          <label class="label" for="wm-width">图片宽度</label>
          <span class="text-[12.5px] text-muted tabular">页宽的 {imageWidth}%</span>
        </div>
        <input id="wm-width" type="range" min="5" max="100" step="5" class="w-full accent-accent" bind:value={imageWidth} />
      </div>
    {/if}

    <div>
      <div class="flex items-baseline justify-between">
        <label class="label" for="wm-opacity">不透明度</label>
        <span class="text-[12.5px] text-muted tabular">{opacity}%</span>
      </div>
      <input id="wm-opacity" type="range" min="5" max="100" step="5" class="w-full accent-accent" bind:value={opacity} />
    </div>
    <div>
      <span class="label">旋转角度</span>
      <Segmented bind:value={angles[kind]} size="sm" ariaLabel="旋转角度" options={ANGLES} />
    </div>
    <div>
      <span class="label">位置</span>
      <Segmented
        bind:value={layout}
        size="sm"
        ariaLabel="位置"
        options={[
          { value: 'center', label: '页面居中' },
          { value: 'tile', label: '平铺满页' },
        ]}
      />
    </div>
    <div>
      <label class="label" for="wm-pages">页码范围 <span class="font-normal text-muted">（可选）</span></label>
      <input id="wm-pages" class="field font-mono text-[13px] placeholder:font-sans" placeholder="全部页，或如 1-3,5,8-" bind:value={pages} aria-invalid={!!pagesError} />
      {#if pagesError}<p class="mt-1 text-[12px] text-bad-ink">{pagesError}</p>{/if}
    </div>
  {/snippet}

  {#snippet summary()}
    <div class="space-y-3">
      {#if missing.length}
        <p class="flex items-start gap-1.5 text-warn-ink">
          <TriangleAlert class="mt-0.5 size-4 shrink-0" />
          <span>字体里没有这些字符，水印中会显示为空白：{missing.join(' ')}。可以换一种字体再试</span>
        </p>
      {/if}
      {#if wasEncrypted}
        <p class="flex items-start gap-1.5 text-muted">
          <Info class="mt-0.5 size-4 shrink-0" />
          <span>原文件带有加密，生成的文件不再加密；如需保护，可以再用「加密 PDF」设置密码</span>
        </p>
      {/if}
      {#if preview}
        <figure class="animate-pop">
          <img
            src={preview.url}
            alt="第 {preview.page} 页加水印后的效果"
            class="max-h-[460px] w-auto max-w-full rounded-lg border border-line bg-white shadow-card"
          />
          <figcaption class="mt-1.5 text-[12px] text-muted">第 {preview.page} 页效果预览</figcaption>
        </figure>
      {/if}
    </div>
  {/snippet}
</ToolFrame>
