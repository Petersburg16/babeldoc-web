<script lang="ts">
  import { onDestroy } from 'svelte';
  import Switch from '../../../components/Switch.svelte';
  import { TriangleAlert } from '../../../lib/icons';
  import { engines } from '../../../lib/pdf/engines.svelte';
  import { readBytes, type Report } from '../../../lib/pdf/files';
  import { CloudDownload } from '../../../lib/pdf/icons';
  import { Cancelled } from '../../../lib/pdf/input';
  import { ALL_FONT_KEYS, fontEngineIds } from '../../../lib/pdf/office/fonts';
  import type { OfficeFailure } from '../../../lib/pdf/ops/office';
  import FilePicker from '../ui/FilePicker.svelte';
  import ToolFrame from '../ui/ToolFrame.svelte';

  const ACCEPT = '.docx,.doc,.xlsx,.xls,.pptx,.ppt,.odt,.ods,.odp,.rtf,.txt,.csv';
  const CJK_FONTS = fontEngineIds(['cjk']);
  const ALL_FONTS = fontEngineIds(ALL_FONT_KEYS);
  const EXTRA_FONTS = [
    ['kai', '楷体'],
    ['fang', '仿宋'],
  ] as const;
  const SUBSTITUTES = [
    ['宋体', '思源宋体'],
    ['黑体、微软雅黑、等线', '思源黑体'],
    ['楷体', '霞鹜文楷'],
    ['仿宋', '朱雀仿宋'],
  ];

  // LibreOffice 要 SharedArrayBuffer（跨源隔离）和约 1.5 GB 内存，手机上基本跑不动
  const isolated = globalThis.crossOriginIsolated === true && typeof SharedArrayBuffer !== 'undefined';
  const ua = navigator as Navigator & { userAgentData?: { mobile?: boolean } };
  const mobile =
    ua.userAgentData?.mobile === true ||
    /Android.+Mobile|iPhone|iPod|Windows Phone|HarmonyOS.+Mobile/i.test(navigator.userAgent) ||
    matchMedia('(max-width: 640px) and (pointer: coarse)').matches;

  let files = $state<File[]>([]);
  let pdfa = $state(false);
  let failed = $state<OfficeFailure[]>([]);
  // 选好文件就先检查空文件、加了密码、不是 Office 文件等，不必等下载完引擎才知道；'' 表示可以转换
  let problems = $state<Record<string, string>>({});
  const queued = new Set<string>();
  let checks = Promise.resolve();
  // 离开页面：还没开始的转换不再启动引擎，已启动的立即关掉
  const leaving = new AbortController();
  let started = false;

  const key = (f: File) => f.name + f.size;
  const bad = $derived(files.filter((f) => problems[key(f)]));
  const usable = $derived(files.length - bad.length);
  // 选文件时已在上方提示过的不再在结果里重复
  const runFailed = $derived(failed.filter((f) => !bad.some((b) => b.name === f.name)));

  $effect(() => {
    for (const file of files) {
      const k = key(file);
      if (queued.has(k)) continue;
      queued.add(k);
      // 逐个读，一次选很多大文件时不同时占内存
      checks = checks.then(async () => {
        problems[k] = await inspect(file);
      });
    }
  });

  async function inspect(file: File) {
    try {
      const { inspectOffice } = await import('../../../lib/pdf/office/sniff');
      return inspectOffice(file.name, await readBytes(file)).problem ?? '';
    } catch (e) {
      console.error(e);
      return ''; // 检查不了的留到转换时再报错
    }
  }

  // 与引擎提示一样按 1024 进位，两个数字放在一起时口径一致
  const mb = (n: number) => Math.max(1, Math.round(n / 1024 / 1024));
  const fontMin = $derived(engines.pendingBytes(CJK_FONTS));
  const fontMax = $derived(engines.pendingBytes(ALL_FONTS));
  const fontHint = $derived.by(() => {
    if (fontMin >= 1_000_000) {
      return `中文文档需下载中文字体，约 ${fontMin === fontMax ? mb(fontMax) : `${mb(fontMin)}–${mb(fontMax)}`} MB`;
    }
    // 思源字体已缓存，只差楷体、仿宋：普通中文文档不用再下载
    if (fontMax - fontMin >= 1_000_000) {
      const names = EXTRA_FONTS.filter(([k]) => engines.pendingBytes(fontEngineIds([k])) > 0).map(([, name]) => name);
      return `用到${names.join('、')}时需再下载字体，约 ${mb(fontMax - fontMin)} MB`;
    }
    return '';
  });

  const blocked = $derived(
    !isolated
      ? '当前浏览器不支持，请换用电脑上的新版浏览器'
      : !files.length
        ? '请先选择文件'
        : files.length > 1
          ? '所选文件都无法转换'
          : '这个文件无法转换',
  );

  async function run(report: Report) {
    // ToolFrame 下载完引擎才调用这里：下载途中已离开页面的，不再启动 LibreOffice
    if (leaving.signal.aborted) throw new Cancelled();
    failed = [];
    started = true;
    const list = [...files];
    const { officeToPdf } = await import('../../../lib/pdf/ops/office');
    const result = await officeToPdf(list, { pdfa }, report, leaving.signal);
    failed = result.failed;
    return result.outputs;
  }

  onDestroy(() => {
    leaving.abort();
    if (started) void import('../../../lib/pdf/office/engine').then((m) => m.releaseOffice());
  });
</script>

<ToolFrame
  engines={['libreoffice']}
  runLabel={usable > 1 ? `转换 ${usable} 个文件` : '转为 PDF'}
  canRun={isolated && usable > 0}
  {blocked}
  zipName="Office 转 PDF.zip"
  summary={runFailed.length ? failures : undefined}
  onrun={run}
  onreset={() => {
    files = [];
    failed = [];
  }}
>
  {#snippet input()}
    {#if !isolated || mobile}
      <div class="mb-3 flex gap-2.5 rounded-xl bg-warn-soft px-4 py-3 text-[13px] leading-relaxed text-warn-ink" role="note">
        <TriangleAlert class="mt-0.5 size-4 shrink-0" />
        <p>
          {isolated ? '手机上可能因内存不足而转换失败。' : '当前浏览器无法运行转换引擎。'}需要电脑上的 Chrome/Edge/Firefox 最新版，转换时约占
          1.5 GB 内存。
        </p>
      </div>
    {/if}
    <FilePicker
      bind:files
      accept={ACCEPT}
      kind="Office 文件"
      multiple
      maxMb={200}
      hint="支持 Word、Excel、PowerPoint、OpenDocument、RTF、TXT、CSV，可一次选择多个"
    />
    {#if bad.length}
      <div class="mt-3 rounded-xl bg-warn-soft px-4 py-3 text-[13px] leading-relaxed text-warn-ink" aria-live="polite">
        <p class="flex items-center gap-2 font-medium">
          <TriangleAlert class="size-4 shrink-0" />
          {usable ? `${bad.length} 个文件无法转换，转换时会跳过` : bad.length > 1 ? '这些文件都无法转换' : '这个文件无法转换'}
        </p>
        <ul class="mt-1 space-y-0.5 pl-6">
          {#each bad as file (key(file))}
            <li>{problems[key(file)]}</li>
          {/each}
        </ul>
      </div>
    {/if}
  {/snippet}
  {#snippet options()}
    <Switch bind:checked={pdfa} label="转为 PDF/A" description="长期归档格式（PDF/A-2b），提交学位论文、档案时常用" />
    <div>
      <p class="label">中文字体替换</p>
      <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-[12.5px]">
        {#each SUBSTITUTES as [from, to] (from)}
          <dt class="text-ink-2">{from}</dt>
          <dd class="text-muted">→ {to}</dd>
        {/each}
      </dl>
      <p class="hint leading-snug">
        浏览器里没有 Windows 字体，换用字形相近的开源字体；Times New Roman、Arial、Calibri 等换成字宽相同的字体。行距和分页可能与原文件略有不同。
      </p>
    </div>
    {#if fontHint}
      <p class="flex items-start gap-1.5 text-[12.5px] leading-snug text-muted">
        <CloudDownload class="mt-px size-3.5 shrink-0" />
        {fontHint}
      </p>
    {/if}
  {/snippet}
</ToolFrame>

{#snippet failures()}
  <p class="mb-1.5">{runFailed.length} 个文件没有转换：</p>
  <ul class="space-y-1">
    {#each runFailed as f, i (i)}
      <li class="rounded-lg bg-warn-soft px-3 py-1.5 text-[12.5px] text-warn-ink">{f.reason}</li>
    {/each}
  </ul>
{/snippet}
