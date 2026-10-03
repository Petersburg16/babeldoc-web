<script lang="ts">
  import Segmented from '../../../components/Segmented.svelte';
  import { pdfBlob, stem, type Report } from '../../../lib/pdf/files';
  import type { Orientation, PaperSize } from '../../../lib/pdf/ops/images';
  import FilePicker from '../ui/FilePicker.svelte';
  import ToolFrame from '../ui/ToolFrame.svelte';

  // 浏览器能解码的常见格式。HEIC、TIFF 不放进 accept（accept 含 HEIC 时 iOS 不再把相册照片自动转成 JPEG），
  // 但拖进来的仍然收下：浏览器能解码就照常转换，否则运行时提示“请先转成 JPG”，而不是说它不是图片
  const EXTS = ['.jpg', '.jpeg', '.jfif', '.png', '.webp', '.gif', '.bmp', '.avif'];
  const ACCEPT = ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/bmp', 'image/avif', ...EXTS].join(',');
  const match = (file: File) => [...EXTS, '.heic', '.heif', '.tif', '.tiff'].some((ext) => file.name.toLowerCase().endsWith(ext));

  let files = $state<File[]>([]);
  let paper = $state<PaperSize>('A4');
  let orientation = $state<Orientation>('auto');
  let margin = $state<'0' | '10' | '20'>('0');

  const fit = $derived(paper === 'fit');

  async function run(report: Report) {
    // 处理期间列表仍可编辑，先固定这一次的输入
    const list = [...files];
    const opts = { paper, orientation, marginMm: Number(margin) };
    const { imagesToPdf } = await import('../../../lib/pdf/ops/images');
    const out = await imagesToPdf(list, opts, report);
    const name = list.length === 1 ? `${stem(list[0].name)}.pdf` : '图片合集.pdf';
    return [{ name, blob: pdfBlob(out) }];
  }
</script>

<ToolFrame
  engines={['core']}
  runLabel={files.length > 1 ? `合成 ${files.length} 张图片` : '转为 PDF'}
  canRun={files.length > 0}
  blocked="请先选择图片"
  onrun={run}
  onreset={() => (files = [])}
>
  {#snippet input()}
    <FilePicker
      bind:files
      accept={ACCEPT}
      {match}
      kind="图片"
      multiple
      reorderable
      hint="支持 JPG、PNG、WebP、GIF、BMP、AVIF，每张一页，拖动或用箭头调整顺序"
    />
  {/snippet}
  {#snippet options()}
    <div>
      <span class="label">页面尺寸</span>
      <Segmented
        bind:value={paper}
        ariaLabel="页面尺寸"
        options={[
          { value: 'fit', label: '跟随图片' },
          { value: 'A4', label: 'A4' },
          { value: 'Letter', label: 'Letter' },
        ]}
      />
      <p class="hint">{fit ? '每页与图片同样比例，按图片自带的分辨率换算大小' : '图片等比缩放、居中放在页面上'}</p>
    </div>
    <div>
      <span class="label">页面方向</span>
      <Segmented
        bind:value={orientation}
        ariaLabel="页面方向"
        options={[
          { value: 'auto', label: '自动', disabled: fit },
          { value: 'portrait', label: '纵向', disabled: fit },
          { value: 'landscape', label: '横向', disabled: fit },
        ]}
      />
      <p class="hint">{fit ? '跟随图片时方向与图片一致' : '自动：横图用横向页面，竖图用纵向页面'}</p>
    </div>
    <div>
      <span class="label">边距</span>
      <Segmented
        bind:value={margin}
        ariaLabel="边距"
        options={[
          { value: '0', label: '无' },
          { value: '10', label: '小' },
          { value: '20', label: '大' },
        ]}
      />
      <p class="hint">{margin === '0' ? '不留边距' : `四周各留 ${margin} mm`}</p>
    </div>
  {/snippet}
</ToolFrame>
