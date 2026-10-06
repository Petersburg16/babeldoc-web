<script lang="ts">
  import { tick, untrack } from 'svelte';
  import { SvelteMap } from 'svelte/reactivity';
  import FilePicker from '../../../components/FilePicker.svelte';
  import ProgressBar from '../../../components/ProgressBar.svelte';
  import { confirm } from '../../../lib/confirm.svelte';
  import { spaced } from '../../../lib/format';
  import { CircleAlert, Copy, LoaderCircle, RotateCcw, Trash2 } from '../../../lib/icons';
  import { engines } from '../../../lib/pdf/engines.svelte';
  import { pdfBlob, renamed, type Report } from '../../../lib/pdf/files';
  import { ArrowDownUp, FilePlus, RotateCw, Undo2 } from '../../../lib/pdf/icons';
  import { Cancelled, unlockPdf, unreadable } from '../../../lib/pdf/input';
  import type { PageInfo, PageSource, Slot } from '../../../lib/pdf/ops/organize';
  import { normalizeRotation } from '../../../lib/pdf/ops/pages';
  import { passwordState } from '../../../lib/pdf/password.svelte';
  import { currentTool } from '../../../lib/pdf/tools';
  import { toast } from '../../../lib/toast.svelte';
  import PageGrid, { duplicateSlots, newSlotId, rotateSlots, shownSize } from '../ui/PageGrid.svelte';
  import ToolFrame from '../ui/ToolFrame.svelte';

  interface Source {
    /** 已解密的原文件 */
    bytes: Uint8Array;
    encrypted: boolean;
    pages: PageInfo[];
    view: PageSource;
  }

  // 选好文件就要用引擎，不等点开始；与 ToolFrame 一样取自工具清单
  const ENGINES = currentTool().engines;

  let files = $state<File[]>([]);
  let phase = $state<'idle' | 'download' | 'open' | 'ready' | 'error'>('idle');
  let progress = $state<number | null>(null);
  let loadError = $state('');
  let retryable = $state(false);
  let attempt = $state(0);
  let source = $state.raw<Source | null>(null);
  const thumbs = new SvelteMap<number, string>();
  let initial = $state.raw<Slot[]>([]);
  let items = $state.raw<Slot[]>([]);
  let history = $state.raw<Slot[][]>([]);
  let selected = $state<string[]>([]);
  let message = $state('');
  let panel = $state<HTMLElement>();

  const chosen = $derived(new Set(selected));
  const signature = (list: Slot[]) =>
    list.map((s) => (s.kind === 'page' ? `${s.index}:${normalizeRotation(s.rotate)}` : 'b')).join(',');
  const changed = $derived(signature(items) !== signature(initial));
  const hasPage = $derived(items.some((s) => s.kind === 'page'));
  const engineLabel = $derived(spaced(engines.label(ENGINES)));

  const stats = $derived.by(() => {
    const order = items.filter((s) => s.kind === 'page').map((s) => s.index);
    const kept = new Set(order);
    const parts: string[] = [];
    const deleted = (source?.pages.length ?? 0) - kept.size;
    const firsts = order.filter((p, i) => order.indexOf(p) === i);
    if (firsts.some((p, i) => i > 0 && p < firsts[i - 1])) parts.push('顺序已调整');
    if (deleted) parts.push(`删除 ${deleted} 页`);
    const rotated = items.filter((s) => normalizeRotation(s.rotate)).length;
    if (rotated) parts.push(`旋转 ${rotated} 页`);
    if (order.length > kept.size) parts.push(`复制 ${order.length - kept.size} 页`);
    const blanks = items.length - order.length;
    if (blanks) parts.push(`空白页 ${blanks} 张`);
    return parts.join(' · ');
  });

  // 选好文件就准备引擎、解密并读出各页尺寸；缩略图随滚动按需渲染
  $effect(() => {
    const file = files[0];
    void attempt;
    if (!file) return;
    let alive = true;
    let view: PageSource | null = null;
    untrack(() => {
      void load(file, () => alive).then((v) => {
        if (alive) view = v;
        else void v?.dispose();
      });
    });
    return () => {
      alive = false;
      // 换文件时还开着的密码框属于旧文件，关掉
      if (passwordState.current?.filename === file.name) passwordState.answer(null);
      void view?.dispose();
      thumbs.clear();
      source = null;
      initial = [];
      items = [];
      history = [];
      selected = [];
      message = '';
      loadError = '';
      phase = 'idle';
    };
  });

  async function load(file: File, alive: () => boolean): Promise<PageSource | null> {
    loadError = '';
    phase = 'download';
    progress = engines.pendingBytes(ENGINES) > 0 ? 0 : null;
    try {
      await engines.ensure(ENGINES, (p) => {
        if (alive()) progress = p.total ? p.loaded / p.total : null;
      });
    } catch (e) {
      console.warn(e);
      if (alive()) fail(downloadError(e), true);
      return null;
    }
    if (!alive()) return null;
    phase = 'open';
    progress = null;
    let unlocked: { bytes: Uint8Array; encrypted: boolean };
    let view: PageSource;
    try {
      const { openPages } = await import('../../../lib/pdf/ops/organize');
      unlocked = await unlockPdf(file);
      if (!alive()) return null;
      view = await openPages(unlocked.bytes, (index, url) => {
        if (alive()) thumbs.set(index, url ?? '');
      });
    } catch (e) {
      if (!alive()) return null;
      if (e instanceof Cancelled) {
        files = [];
        return null;
      }
      console.warn(e);
      fail(unreadable(file), false);
      return null;
    }
    if (!alive()) {
      await view.dispose();
      return null;
    }
    source = { bytes: unlocked.bytes, encrypted: unlocked.encrypted, pages: view.pages, view };
    initial = view.pages.map((_, index) => ({ id: `p${index}`, kind: 'page', index, rotate: 0 }));
    items = initial;
    history = [];
    selected = [];
    message = '';
    phase = 'ready';
    return view;
  }

  /** 引擎自己抛的错误已是中文（HTTP 状态、下载不完整）；断网时浏览器只给英文的 TypeError，换成中文 */
  function downloadError(e: unknown) {
    const text = e instanceof Error ? e.message : '';
    if (e instanceof DOMException && e.name === 'QuotaExceededError') return '引擎下载失败：浏览器存储空间不足，请清理后重试';
    if (e instanceof TypeError) return '引擎下载失败：网络连接中断，请检查网络后重试';
    if (!/[一-鿿]/.test(text)) return '引擎下载失败，请检查网络后重试';
    return text.includes('重试') ? text : `${text}，请稍后重试`;
  }

  function fail(text: string, canRetry: boolean) {
    loadError = text;
    retryable = canRetry;
    phase = 'error';
  }

  /** 每次改动都记入撤销历史 */
  function commit(next: Slot[], text: string) {
    history = [...history.slice(-99), items];
    items = next;
    message = text;
    const ids = new Set(next.map((s) => s.id));
    if (selected.some((id) => !ids.has(id))) selected = selected.filter((id) => ids.has(id));
  }

  function undo() {
    if (!history.length) return;
    items = history[history.length - 1];
    history = history.slice(0, -1);
    const ids = new Set(items.map((s) => s.id));
    selected = selected.filter((id) => ids.has(id));
    message = '已撤销上一步';
  }

  function rotateSelected(delta: number) {
    commit(rotateSlots(items, chosen, delta), `已把 ${selected.length} 页${delta < 0 ? '向左' : '向右'}旋转`);
  }

  async function deleteSelected() {
    const at = items.findIndex((s) => chosen.has(s.id));
    const n = items.filter((s) => chosen.has(s.id)).length;
    commit(
      items.filter((s) => !chosen.has(s.id)),
      `已删除 ${n} 页`,
    );
    selected = [];
    // 被删的卡片和工具栏按钮都没了，焦点放到原位置的卡片上，键盘和读屏用户不用从页首找回来
    await tick();
    const cards = panel?.querySelectorAll<HTMLElement>('[data-scroller] [data-slot]');
    if (cards?.length) cards[Math.min(at, cards.length - 1)].querySelector<HTMLButtonElement>('[data-select]')?.focus();
    else panel?.querySelector<HTMLButtonElement>('[data-undo-empty]')?.focus();
  }

  async function clearSelection() {
    selected = [];
    await tick();
    panel?.querySelector<HTMLButtonElement>('[data-select-all]')?.focus();
  }

  // 网格之外没有接收系统文件的地方，Chrome 会在当前标签直接打开拖进来的文件，调整全部丢失。
  // 整页接住：拖入 PDF 视为换文件，有还没导出的调整时先确认
  function onDragOver(event: DragEvent) {
    if (event.defaultPrevented || !event.dataTransfer?.types.includes('Files')) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  }

  async function onDrop(event: DragEvent) {
    if (event.defaultPrevented || !event.dataTransfer?.types.includes('Files')) return;
    event.preventDefault();
    // 事件结束后 DataTransfer 会被清空，先把文件取出来
    const dropped = Array.from(event.dataTransfer.files);
    const file = dropped.find((f) => /\.pdf$/i.test(f.name));
    if (!file) {
      if (dropped.length) toast.info('只能拖入 PDF 文件');
      return;
    }
    if (file.size > 500 * 1024 * 1024) {
      toast.error(`${file.name} 超过 500 MB，未添加`);
      return;
    }
    const current = files[0];
    if (current && current.name === file.name && current.size === file.size) return;
    if (current && phase === 'ready' && changed) {
      const ok = await confirm({
        title: `换成「${file.name}」？`,
        message: '当前的页面调整还没有导出，换文件后会丢失。',
        confirmText: '换文件',
        danger: true,
      });
      if (!ok) return;
    }
    files = [file];
    if (current) toast.info(dropped.length > 1 ? `一次整理一个 PDF，已换成「${file.name}」` : `已换成「${file.name}」`);
  }

  async function insertBlank() {
    if (!source) return;
    // 有选中页时插在最后一个选中页之后，否则插在末尾；尺寸取前一页（按当前看到的方向）
    let at = items.length;
    for (let i = items.length - 1; i >= 0; i--) {
      if (chosen.has(items[i].id)) {
        at = i + 1;
        break;
      }
    }
    const neighbour = items[at - 1] ?? items[at];
    const size = neighbour ? shownSize(neighbour, source.pages) : { width: 595.28, height: 841.89 };
    const blank: Slot = { id: newSlotId(), kind: 'blank', width: size.width, height: size.height, rotate: 0 };
    commit([...items.slice(0, at), blank, ...items.slice(at)], `已在第 ${at + 1} 页插入空白页`);
    selected = [blank.id];
    await tick();
    document.querySelector(`[data-slot="${blank.id}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function onKey(event: KeyboardEvent) {
    if (phase !== 'ready' || passwordState.current) return;
    const target = event.target as HTMLElement | null;
    if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
    if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'z') {
      if (history.length) {
        event.preventDefault();
        undo();
      }
    } else if (target?.closest('[data-scroller]') && selected.length) {
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        void deleteSelected();
      } else if (event.key === 'Escape') {
        selected = [];
      }
    }
  }

  async function run(report: Report) {
    const src = source;
    const file = files[0];
    const slots = items; // 处理期间继续编辑不影响这一次的结果
    if (!src || !file) throw new Error('请先选择一个 PDF');
    const { organizePdf } = await import('../../../lib/pdf/ops/organize');
    report(null, `正在生成 ${slots.length} 页`);
    const out = await organizePdf(src.bytes, slots);
    return [{ name: renamed(file.name, '整理'), blob: pdfBlob(out) }];
  }

  const blocked = $derived(
    !files.length
      ? '请先选择一个 PDF'
      : phase === 'error'
        ? '文件没有读取成功'
        : phase !== 'ready'
          ? '正在读取页面…'
          : !hasPage
            ? '至少保留原文件的一页'
            : '还没有调整页面',
  );
</script>

<svelte:window onkeydown={onKey} ondragover={onDragOver} ondrop={onDrop} />

<ToolFrame
  resetKey={files}
  stacked
  runLabel="导出整理后的 PDF"
  canRun={phase === 'ready' && hasPage && changed}
  {blocked}
  onrun={run}
  onreset={() => (files = [])}
>
  {#snippet input()}
    <div class="space-y-3">
      <FilePicker bind:files accept="application/pdf,.pdf" hint="选择一个 PDF，之后可以拖动缩略图排序，也可以旋转、复制或删除页面" />

      {#if files.length}
        <section bind:this={panel} class="card overflow-hidden" aria-label="页面整理">
          {#if phase === 'ready' && source}
            <div class="flex flex-wrap items-center gap-x-1 gap-y-1.5 border-b border-line px-3 py-2 sm:px-4">
              <p class="mr-auto pr-2 text-[13px] text-ink-2">
                {#if selected.length}
                  已选 <span class="font-medium text-ink tabular">{selected.length}</span> 页
                {:else}
                  共 <span class="tabular">{items.length}</span> 页<span class="hidden text-muted md:inline"
                    >，点击缩略图可多选，按住 Shift 连选</span
                  >
                {/if}
              </p>
              {#if selected.length}
                <button class="btn btn-ghost btn-sm" onclick={() => rotateSelected(-90)}><RotateCcw class="size-3.5" /><span class="max-sm:sr-only">左转</span></button>
                <button class="btn btn-ghost btn-sm" onclick={() => rotateSelected(90)}><RotateCw class="size-3.5" /><span class="max-sm:sr-only">右转</span></button>
                <button class="btn btn-ghost btn-sm" onclick={() => commit(duplicateSlots(items, chosen), `已复制 ${selected.length} 页`)}>
                  <Copy class="size-3.5" /><span class="max-sm:sr-only">复制</span>
                </button>
                <button class="btn btn-ghost btn-sm hover:text-bad-ink" onclick={deleteSelected}><Trash2 class="size-3.5" /><span class="max-sm:sr-only">删除</span></button>
                <button class="btn btn-ghost btn-sm" onclick={clearSelection}>取消选择</button>
                <span class="mx-1 h-5 w-px bg-line max-sm:hidden" aria-hidden="true"></span>
              {:else if items.length}
                <button data-select-all class="btn btn-ghost btn-sm" onclick={() => (selected = items.map((s) => s.id))}>全选</button>
              {/if}
              <button class="btn btn-ghost btn-sm" title="有选中页时插在其后，否则插在末尾；尺寸与前一页相同" onclick={insertBlank}>
                <FilePlus class="size-3.5" /><span class="max-sm:sr-only">空白页</span>
              </button>
              <button
                class="btn btn-ghost btn-sm"
                disabled={items.length < 2}
                onclick={() => commit([...items].reverse(), '已倒序排列')}
              >
                <ArrowDownUp class="size-3.5" /><span class="max-sm:sr-only">倒序</span>
              </button>
              <button class="btn btn-ghost btn-sm" title="撤销（Ctrl+Z）" disabled={!history.length} onclick={undo}>
                <Undo2 class="size-3.5" /><span class="max-sm:sr-only">撤销</span>
              </button>
              <button
                class="btn btn-ghost btn-sm"
                disabled={!changed}
                onclick={() => {
                  commit(initial, '已恢复为原文件的页面');
                  selected = [];
                }}
              >
                重置
              </button>
            </div>

            {#if items.length}
              <PageGrid
                {items}
                pages={source.pages}
                {thumbs}
                bind:selected
                onchange={commit}
                onvisible={(index, visible) => source?.view.want(index, visible)}
              />
            {:else}
              <div class="px-6 py-14 text-center">
                <p class="text-[13.5px] font-medium">所有页面都已删除</p>
                <button data-undo-empty class="btn btn-secondary btn-sm mt-3" onclick={undo}><Undo2 class="size-3.5" /> 撤销</button>
              </div>
            {/if}

            <div class="flex min-h-10 items-center gap-2 border-t border-line px-3 py-1.5 text-[12.5px] text-muted sm:px-4" aria-live="polite">
              <span class="min-w-0 flex-1 truncate">{message || '拖动缩略图调整顺序，也可以用每页下方的箭头'}</span>
              {#if message && history.length}
                <button class="btn btn-ghost btn-sm" onclick={undo}><Undo2 class="size-3.5" /> 撤销</button>
              {/if}
            </div>
          {:else if phase === 'error'}
            <div class="m-4 flex flex-wrap items-start gap-2 rounded-lg bg-bad-soft px-3 py-2.5 text-[13px] text-bad-ink" role="alert">
              <CircleAlert class="mt-0.5 size-4 shrink-0" />
              <span class="min-w-0 flex-1 break-words">{loadError}</span>
              {#if retryable}
                <button class="btn btn-secondary btn-sm" onclick={() => attempt++}>重试</button>
              {:else}
                <button class="btn btn-secondary btn-sm" onclick={() => (files = [])}>换一个文件</button>
              {/if}
            </div>
          {:else}
            <div class="flex flex-col items-center gap-3 px-6 py-14 text-center" aria-live="polite">
              {#if phase === 'download' && progress !== null}
                <div class="w-full max-w-xs space-y-1.5">
                  <ProgressBar value={progress * 100} label="引擎下载进度" />
                  <p class="text-[12.5px] text-muted">正在下载{engineLabel} {Math.round(progress * 100)}%</p>
                </div>
              {:else}
                <LoaderCircle class="size-5 animate-spin text-accent" />
                <p class="text-[13px] text-muted">正在读取页面…</p>
              {/if}
            </div>
          {/if}
        </section>
      {/if}
    </div>
  {/snippet}
  {#snippet options()}
    {#if phase === 'ready'}
      <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p class="text-[14px] font-medium">将导出 <span class="tabular">{items.length}</span> 页</p>
        <p class="text-[12.5px] text-muted">{stats || '还没有调整'}</p>
      </div>
      {#if source?.encrypted}
        <p class="hint">原文件带有加密，导出的文件不再设密码或权限限制</p>
      {/if}
    {:else}
      <p class="text-[13px] text-muted">选择 PDF 后，在缩略图上调整页面，再导出新文件</p>
    {/if}
  {/snippet}
</ToolFrame>
