<script lang="ts">
  import { untrack } from 'svelte';
  import FilePicker from '../../../components/FilePicker.svelte';
  import Switch from '../../../components/Switch.svelte';
  import { errorText } from '../../../lib/format';
  import { CircleAlert, LoaderCircle, Lock, Plus, RotateCcw, Trash2, TriangleAlert } from '../../../lib/icons';
  import { engines } from '../../../lib/pdf/engines.svelte';
  import { pdfBlob, renamed, type Report } from '../../../lib/pdf/files';
  import { Cancelled, unlockPdf } from '../../../lib/pdf/input';
  import type { MetadataEdit, PdfMetadata, TextField } from '../../../lib/pdf/ops/metadata';
  import ToolFrame from '../ui/ToolFrame.svelte';

  const TEXT: { id: TextField; label: string; wide?: boolean; hint?: string }[] = [
    { id: 'title', label: '标题', wide: true },
    { id: 'author', label: '作者' },
    { id: 'subject', label: '主题' },
    { id: 'keywords', label: '关键词', wide: true, hint: '多个关键词用逗号分隔' },
    { id: 'creator', label: '创建程序' },
    { id: 'producer', label: '生成程序' },
  ];
  const STANDARD_KEYS = ['Title', 'Author', 'Subject', 'Keywords', 'Creator', 'Producer', 'CreationDate', 'ModDate', 'Trapped'];

  interface Row {
    id: number;
    key: string;
    value: string;
  }
  interface Form {
    text: Record<TextField, string>;
    creationDate: string;
    modDate: string;
    custom: Row[];
  }
  interface Source {
    file: File;
    /** 已解密的字节，保存时直接用，不再问一次密码 */
    bytes: Uint8Array;
    encrypted: boolean;
    meta: PdfMetadata;
  }

  let files = $state<File[]>([]);
  let source = $state.raw<Source | null>(null);
  let loading = $state(false);
  let loadError = $state('');
  let clearAll = $state(false);
  let form = $state<Form>(blankForm());
  let initial = $state.raw<Form>(blankForm());
  let done = $state<'edit' | 'clear'>('edit');
  let rowId = 0;
  let token = 0;

  function blankForm(): Form {
    return {
      text: { title: '', author: '', subject: '', keywords: '', creator: '', producer: '' },
      creationDate: '',
      modDate: '',
      custom: [],
    };
  }

  const pad = (n: number) => String(n).padStart(2, '0');
  // datetime-local 用本地时间，精确到秒；整分时浏览器会省掉秒，这里也省掉，免得没改也算作修改
  const toInput = (d: Date | null) =>
    d
      ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}` +
        (d.getSeconds() ? `:${pad(d.getSeconds())}` : '')
      : '';
  const fromInput = (s: string) => {
    const d = s ? new Date(s) : null;
    return d && !Number.isNaN(d.getTime()) ? d : null;
  };

  function formFrom(meta: PdfMetadata): Form {
    return {
      text: { ...meta.text },
      creationDate: toInput(meta.creationDate),
      modDate: toInput(meta.modDate),
      custom: meta.custom.map(([key, value]) => ({ id: ++rowId, key, value })),
    };
  }

  const customKey = (rows: Row[]) =>
    JSON.stringify(rows.map((r) => [r.key.trim(), r.value.trim()]).filter(([k, v]) => k || v));

  /** 只收集改过的字段：没动过的日期保留原来的时区和秒，生成程序也不会被改写 */
  const edit = $derived.by((): MetadataEdit | null => {
    const text: MetadataEdit['text'] = {};
    for (const { id } of TEXT) if (form.text[id].trim() !== initial.text[id].trim()) text[id] = form.text[id];
    const out: MetadataEdit = { text };
    if (form.creationDate !== initial.creationDate) out.creationDate = fromInput(form.creationDate);
    if (form.modDate !== initial.modDate) out.modDate = fromInput(form.modDate);
    if (customKey(form.custom) !== customKey(initial.custom)) out.custom = form.custom.map((r) => [r.key, r.value]);
    const changed = Object.keys(text).length + Number('creationDate' in out) + Number('modDate' in out) + Number(!!out.custom);
    return changed ? out : null;
  });

  const customError = $derived.by(() => {
    // 文件里原本就是空值的字段不算错，没改动时原样保留
    const emptyBefore = new Set(initial.custom.filter((r) => !r.value.trim()).map((r) => r.key.trim()));
    const seen = new Set<string>();
    for (const { key, value } of form.custom) {
      const k = key.trim();
      if (!k && value.trim()) return '自定义字段缺少名称';
      if (!k) continue;
      if (STANDARD_KEYS.includes(k)) return `「${k}」是标准属性名，请换一个名称`;
      if (seen.has(k)) return `自定义字段「${k}」重复了`;
      if (!value.trim() && !emptyBefore.has(k)) return `自定义字段「${k}」缺少内容，不需要的话点右侧删除`;
      seen.add(k);
    }
    return '';
  });

  const pdfa = $derived(source?.meta.pdfa ?? '');
  const pdfua = $derived(source?.meta.pdfua ?? '');
  // pdf-lib 只会按新值重写这几种 PDF/A 的 XMP
  const pdfaSynced = $derived(/^PDF\/A-(1B|2B|2U|3B|3U)$/.test(pdfa));
  const fromXmp = $derived(new Set<string>(source?.meta.fromXmp ?? []));

  $effect(() => {
    const file = files[0];
    untrack(() => {
      if (!file) {
        token++;
        source = null;
        loading = false;
        loadError = '';
      } else if (source?.file !== file) {
        void load(file);
      }
    });
  });

  async function load(file: File) {
    const mine = ++token;
    loading = true;
    loadError = '';
    source = null;
    clearAll = false;
    try {
      if (!file.size) {
        loadError = `「${file.name}」是空文件，请重新选择`;
        return;
      }
      await engines.ensure(['qpdf']);
      const { readMetadata } = await import('../../../lib/pdf/ops/metadata');
      const { bytes, encrypted } = await unlockPdf(file);
      const meta = await readMetadata(bytes);
      if (mine !== token) return;
      source = { file, bytes, encrypted, meta };
      initial = formFrom(meta);
      form = formFrom(meta);
    } catch (e) {
      if (mine !== token) return;
      if (e instanceof Cancelled) loadError = '这个 PDF 有打开密码，输入密码后才能读取属性';
      else {
        console.error(e);
        // qpdf 的原始报错带着 worker 里的临时文件名（/in.pdf: …），给用户看时去掉
        const detail = errorText(e)
          .replace(/^PDF 处理失败：/, '')
          .replace(/\/[^\s:/]+:\s*/g, '');
        loadError = `「${file.name}」读取失败，文件可能已损坏或不是有效的 PDF${detail ? `（${detail}）` : ''}`;
      }
    } finally {
      if (mine === token) loading = false;
    }
  }

  function changed(id: TextField) {
    return form.text[id].trim() !== initial.text[id].trim();
  }

  async function run(report: Report) {
    const src = source!;
    const { clearMetadata, editMetadata } = await import('../../../lib/pdf/ops/metadata');
    report(null, clearAll ? '正在清除元数据' : '正在保存属性');
    const out = clearAll ? (await clearMetadata(src.bytes)).bytes : await editMetadata(src.bytes, edit!);
    done = clearAll ? 'clear' : 'edit';
    return [{ name: renamed(src.file.name, '属性'), blob: pdfBlob(out) }];
  }

  const blocked = $derived(
    !files.length
      ? '先选择一个 PDF'
      : loading
        ? '正在读取文件属性…'
        : !source
          ? '没能读取这个文件的属性'
          : customError || '还没有修改任何属性',
  );
</script>

<ToolFrame
  resetKey={files}
  engines={['qpdf']}
  runLabel={clearAll ? '清除全部元数据' : '保存属性'}
  canRun={!!source && !loading && (clearAll || (!!edit && !customError))}
  {blocked}
  onrun={run}
  onreset={() => (files = [])}
>
  {#snippet input()}
    <div class="space-y-4">
      <FilePicker bind:files accept="application/pdf,.pdf" hint="选择后显示当前的标题、作者等属性，可直接修改" />

      {#if loading}
        <div class="card flex items-center gap-2 p-5 text-[13px] text-muted" aria-live="polite">
          <LoaderCircle class="size-4 animate-spin" /> 正在读取文件属性…
        </div>
      {:else if loadError}
        <div class="card flex flex-wrap items-center gap-3 p-4 text-[13px] text-bad-ink" role="alert">
          <CircleAlert class="size-4 shrink-0" />
          <span class="min-w-0 flex-1 break-words">{loadError}</span>
          <button class="btn btn-secondary btn-sm" onclick={() => files[0] && load(files[0])}>重试</button>
        </div>
      {:else if source}
        <section class="card animate-pop p-4 sm:p-5" aria-label="文档属性">
          <div class="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <h2 class="text-[14.5px] font-semibold">文档属性</h2>
            <span class="text-[12.5px] text-muted tabular">PDF {source.meta.version} · {source.meta.pageCount} 页</span>
            {#each [pdfa, pdfua].filter(Boolean) as std (std)}
              <span class="rounded bg-accent-soft px-1.5 py-px text-[11.5px] font-medium text-accent-ink">{std}</span>
            {/each}
            {#if source.encrypted}
              <span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-px text-[11.5px] text-ink-2">
                <Lock class="size-3" />已加密
              </span>
            {/if}
            {#if edit && !clearAll}
              <button class="btn btn-ghost btn-sm ml-auto" onclick={() => (form = formFrom(source!.meta))}>
                <RotateCcw class="size-3.5" /> 还原
              </button>
            {/if}
          </div>

          {#if clearAll}
            <p class="mt-3 rounded-lg bg-warn-soft px-3 py-2 text-[12.5px] text-warn-ink">
              已开启“清除全部元数据”，下面这些属性都会删除
            </p>
          {/if}

          <fieldset class="mt-4 grid gap-x-4 gap-y-3.5 transition-opacity disabled:opacity-50 sm:grid-cols-2" disabled={clearAll}>
            {#each TEXT as f (f.id)}
              <div class={f.wide ? 'sm:col-span-2' : ''}>
                <div class="flex items-baseline justify-between">
                  <label class="label" for="meta-{f.id}">{f.label}</label>
                  {#if !clearAll && changed(f.id)}
                    <span class="text-[11.5px] text-accent-ink">已修改</span>
                  {:else if !clearAll && fromXmp.has(f.id)}
                    <span class="text-[11.5px] text-muted">取自 XMP</span>
                  {/if}
                </div>
                <input id="meta-{f.id}" class="field" placeholder="未设置" bind:value={form.text[f.id]} />
                {#if f.hint}<p class="hint">{f.hint}</p>{/if}
              </div>
            {/each}

            <div>
              <div class="flex items-baseline justify-between">
                <label class="label" for="meta-created">创建时间</label>
                {#if form.creationDate !== initial.creationDate && !clearAll}
                  <span class="text-[11.5px] text-accent-ink">已修改</span>
                {:else if !clearAll && fromXmp.has('creationDate')}
                  <span class="text-[11.5px] text-muted">取自 XMP</span>
                {/if}
              </div>
              <input id="meta-created" class="field tabular" type="datetime-local" step="1" bind:value={form.creationDate} />
            </div>
            <div>
              <div class="flex items-baseline justify-between">
                <label class="label" for="meta-modified">修改时间</label>
                {#if form.modDate !== initial.modDate && !clearAll}
                  <span class="text-[11.5px] text-accent-ink">已修改</span>
                {:else if !clearAll && fromXmp.has('modDate')}
                  <span class="text-[11.5px] text-muted">取自 XMP</span>
                {/if}
              </div>
              <input id="meta-modified" class="field tabular" type="datetime-local" step="1" bind:value={form.modDate} />
            </div>

            <div class="sm:col-span-2">
              <div class="flex items-center justify-between">
                <span class="label mb-0">自定义字段</span>
                <button
                  type="button"
                  class="btn btn-ghost btn-sm"
                  onclick={() => form.custom.push({ id: ++rowId, key: '', value: '' })}
                >
                  <Plus class="size-3.5" /> 添加
                </button>
              </div>
              {#if form.custom.length}
                <div class="mt-1.5 space-y-2">
                  {#each form.custom as row, i (row.id)}
                    <div class="flex gap-2">
                      <input class="field w-[38%] shrink-0" aria-label="字段名" placeholder="名称" bind:value={row.key} />
                      <input class="field min-w-0 flex-1" aria-label="字段内容" placeholder="内容" bind:value={row.value} />
                      <button
                        type="button"
                        class="btn btn-ghost btn-icon shrink-0"
                        aria-label="删除字段 {row.key}"
                        onclick={() => form.custom.splice(i, 1)}
                      >
                        <Trash2 class="size-4" />
                      </button>
                    </div>
                  {/each}
                </div>
              {:else}
                <p class="text-[12.5px] text-muted">没有自定义字段</p>
              {/if}
              {#if customError && !clearAll}<p class="mt-1.5 text-[12px] text-bad-ink">{customError}</p>{/if}
            </div>
          </fieldset>
        </section>
      {/if}
    </div>
  {/snippet}

  {#snippet options()}
    <Switch
      bind:checked={clearAll}
      disabled={!source}
      label="清除全部元数据"
      description="删除标题、作者、XMP 和文档 ID 等全部属性（不含批注里的作者名）"
    />
    {#if pdfa}
      <p class="flex items-start gap-1.5 rounded-lg bg-warn-soft px-3 py-2 text-[12.5px] leading-snug text-warn-ink">
        <TriangleAlert class="mt-px size-3.5 shrink-0" />
        {clearAll
          ? `这是 ${pdfa} 文件，清除元数据后不再符合 PDF/A 标准`
          : pdfaSynced
            ? `这是 ${pdfa} 文件，XMP 元数据会按新值同步更新`
            : `这是 ${pdfa} 文件，XMP 元数据会保留原样，可能与新属性不一致`}
      </p>
    {:else if pdfua}
      <p class="flex items-start gap-1.5 rounded-lg bg-warn-soft px-3 py-2 text-[12.5px] leading-snug text-warn-ink">
        <TriangleAlert class="mt-px size-3.5 shrink-0" />
        {clearAll
          ? `这是 ${pdfua} 文件，清除元数据后不再符合 PDF/UA 标准`
          : !form.text.title.trim()
            ? `这是 ${pdfua} 文件，PDF/UA 要求有标题，标题留空后不再符合标准`
            : `这是 ${pdfua} 文件，XMP 元数据会按新值重写，并保留 PDF/UA 标识`}
      </p>
    {:else if source?.meta.hasXmp && !clearAll}
      <p class="hint">
        文件里另有一份 XMP 元数据，保存修改时会一并删除，以免阅读器继续显示旧值{fromXmp.size
          ? '；标着“取自 XMP”的属性会转存到文档信息里'
          : ''}
      </p>
    {/if}
    {#if source?.encrypted}
      <p class="hint flex items-start gap-1.5"><Lock class="mt-px size-3.5 shrink-0" />原文件已加密，保存后的文件不再加密</p>
    {/if}
  {/snippet}

  {#snippet summary()}
    {done === 'clear' ? '已删除文档信息、XMP 元数据和文档 ID。' : '已保存修改后的属性。'}
  {/snippet}
</ToolFrame>
