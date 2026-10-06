<script lang="ts">
  import { untrack } from 'svelte';
  import Segmented from '../../components/Segmented.svelte';
  import Switch from '../../components/Switch.svelte';
  import { confirm } from '../../lib/confirm.svelte';
  import { isImeEnter } from '../../lib/format';
  import { LoaderCircle } from '../../lib/icons';
  import { meetingApi } from '../../lib/meeting/api';
  import {
    clock,
    isActive,
    MAX_WRONG_FORMS,
    resolveSpeaker,
    speakerIds,
    speakerName,
    speakerResolver,
    speakerTone,
    splitWrongForms,
  } from '../../lib/meeting/format';
  import { LocateFixed, Mic, PenLine, Play, Undo2, WandSparkles } from '../../lib/meeting/icons';
  import type { MeetingDetail, Segment, TranscriptState } from '../../lib/meeting/types';
  import { meetings } from '../../lib/meetings.svelte';
  import { session } from '../../lib/session.svelte';
  import { toast } from '../../lib/toast.svelte';

  interface Props {
    meeting: MeetingDetail;
    segments: Segment[];
    loading: boolean;
    /** 播放器当前位置（毫秒） */
    currentMs: number;
    playing: boolean;
    /** 逐字稿标签正在显示：隐藏时不跟随滚动 */
    active: boolean;
    onseek: (ms: number) => void;
    onedit: (idx: number, body: { text?: string; speaker?: string }) => Promise<void>;
    onrevert: (idx: number) => Promise<void>;
  }

  let { meeting, segments, loading, currentMs, playing, active, onseek, onedit, onrevert }: Props = $props();

  interface Block {
    key: number;
    speaker: string;
    start: number;
    first: number;
    last: number;
    items: Segment[];
  }

  const FOLLOW_KEY = 'bdw-meeting-follow';
  // 后端约定：说话人传 "new" 表示新建一位说话人
  const NEW_SPEAKER = 'new';
  // 一个人连续说很久时也切成几段：段落不至于太长，content-visibility 的粒度也合适
  const MAX_BLOCK = 24;

  const STATES: Record<TranscriptState, { label: string; cls: string; title: string } | null> = {
    none: null,
    raw: { label: '未整理', cls: 'bg-surface-2 text-ink-2', title: '这是语音识别的原始文字，还没有经过大模型整理' },
    polishing: { label: '整理中', cls: 'bg-accent-soft text-accent-ink', title: '大模型正在整理逐字稿' },
    polished: { label: '已整理', cls: 'bg-good-soft text-good-ink', title: '已由大模型加标点、去口头禅' },
    partial: {
      label: '部分句子保留原文',
      cls: 'bg-warn-soft text-warn-ink',
      title: '有些段落大模型没有整理好，这些句子保留了识别原文',
    },
  };

  let view = $state<'text' | 'raw'>('text');
  let follow = $state(readFollow());
  // 跟随播放时用户自己滚动了页面：先不再自动滚动，显示“回到播放位置”
  let followPaused = $state(false);
  let selected = $state<number | null>(null);
  let editing = $state<number | null>(null);
  let draft = $state('');
  let draftSpeaker = $state('');
  let saving = $state(false);
  let addTerm = $state(false);
  let termText = $state('');
  let termWrong = $state('');
  let polishing = $state(false);
  let root = $state<HTMLElement>();

  const speakers = $derived(meeting.speakers ?? {});
  const showRaw = $derived(view === 'raw');
  const transcriptState = $derived(meeting.transcript_state);
  const stateInfo = $derived(STATES[transcriptState] ?? null);
  const hasPolished = $derived(transcriptState === 'polished' || transcriptState === 'partial');
  const canEdit = $derived(!isActive(meeting) && meeting.op !== 'polish' && transcriptState !== 'polishing');
  const canPolish = $derived(
    meeting.status === 'done' && !meeting.op && transcriptState !== 'polishing' && segments.length > 0,
  );
  const spaced = $derived(meeting.language === 'en');

  const blocks = $derived.by(() => {
    const out: Block[] = [];
    const resolve = speakerResolver(speakers);
    let current: Block | null = null;
    for (const seg of segments) {
      const who = resolve(seg.speaker);
      if (!current || current.speaker !== who || current.items.length >= MAX_BLOCK) {
        current = { key: seg.idx, speaker: who, start: seg.start_ms, first: seg.idx, last: seg.idx, items: [] };
        out.push(current);
      }
      current.items.push(seg);
      current.last = seg.idx;
    }
    return out;
  });

  // 句子可以改归到的说话人：没被合并掉的
  const speakerOptions = $derived(speakerIds(speakers, segments).filter((id) => resolveSpeaker(speakers, id) === id));

  const focusIdx = $derived(editing ?? selected);
  const editingSeg = $derived(editing === null ? null : findSegment(editing));

  function readFollow() {
    try {
      return localStorage.getItem(FOLLOW_KEY) !== '0';
    } catch {
      return true;
    }
  }

  $effect(() => {
    const value = follow;
    untrack(() => {
      followPaused = false;
      try {
        localStorage.setItem(FOLLOW_KEY, value ? '1' : '0');
      } catch {
        /* 忽略 */
      }
    });
  });

  // 重新整理后整理结果没了，原文/整理后的切换跟着收起
  $effect(() => {
    if (!hasPolished) view = 'text';
  });

  function findSegment(idx: number): Segment | null {
    // segments 按 idx 排好序，二分查找
    let lo = 0;
    let hi = segments.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const value = segments[mid].idx;
      if (value === idx) return segments[mid];
      if (value < idx) lo = mid + 1;
      else hi = mid - 1;
    }
    return null;
  }

  /** 播放位置所在的句子：最后一个开始时间不晚于当前位置的句子；之后静音超过 3 秒就不高亮 */
  function locate(ms: number): number {
    let lo = 0;
    let hi = segments.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (segments[mid].start_ms <= ms) {
        found = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    if (found < 0 || ms > segments[found].end_ms + 3000) return -1;
    return segments[found].idx;
  }

  // ---------- 播放高亮：只改一个 DOM 节点的 data-current，不给每句建响应式状态 ----------

  let currentEl: HTMLElement | null = null;
  let currentIdx = -1;
  let scrolledIdx = -1;

  $effect(() => {
    const ms = currentMs;
    const started = playing || ms > 0;
    // 这几个变了都会重建部分 DOM，要重新找节点打标记
    void blocks;
    void focusIdx;
    void showRaw;
    if (!root) return;
    const idx = started ? locate(ms) : -1;
    untrack(() => mark(idx));
  });

  function mark(idx: number) {
    if (currentEl && (currentIdx !== idx || !currentEl.isConnected)) {
      delete currentEl.dataset.current;
      currentEl = null;
    }
    currentIdx = idx;
    if (idx < 0 || !root) return;
    if (!currentEl) {
      currentEl = root.querySelector<HTMLElement>(`[data-idx="${idx}"]`);
      if (currentEl) currentEl.dataset.current = '';
    }
    if (currentEl && idx !== scrolledIdx && follow && !followPaused && active && playing && editing === null) {
      scrolledIdx = idx;
      scrollToCurrent();
    }
  }

  function scrollToCurrent(force = false) {
    if (!currentEl) return;
    const rect = currentEl.getBoundingClientRect();
    // 上方留出站点头部和吸顶播放器的位置
    if (force || rect.top < 180 || rect.bottom > innerHeight - 80) {
      currentEl.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }

  function backToCurrent() {
    followPaused = false;
    scrollToCurrent(true);
  }

  // 只有真正的用户滚动（滚轮、触摸）才暂停跟随；程序滚动不会触发这两个事件
  function onuserscroll() {
    if (follow && playing && active && !followPaused) followPaused = true;
  }

  // ---------- 选中与编辑 ----------

  function onlistclick(event: MouseEvent) {
    const target = event.target as HTMLElement;
    const seekEl = target.closest<HTMLElement>('[data-seek]');
    if (seekEl) {
      followPaused = false;
      onseek(Number(seekEl.dataset.seek));
      return;
    }
    if (target.closest('[data-keep]')) return;
    const sentence = target.closest<HTMLElement>('[data-idx]');
    if (!sentence) {
      if (editing === null) selected = null;
      return;
    }
    const idx = Number(sentence.dataset.idx);
    // 第一下点击会把这一段换成可编辑的写法、节点被替换，dblclick 不一定能触发，所以也按点击次数判断双击
    if (event.detail >= 2) {
      editByDoubleClick(idx);
      return;
    }
    // 拖选文字是想复制，不当作点击
    if (editing !== null || window.getSelection()?.toString()) return;
    selected = selected === idx ? null : idx;
  }

  function onlistdblclick(event: MouseEvent) {
    const sentence = (event.target as HTMLElement).closest<HTMLElement>('[data-idx]');
    if (sentence) editByDoubleClick(Number(sentence.dataset.idx));
  }

  function editByDoubleClick(idx: number) {
    if (!canEdit || showRaw || editing === idx) return;
    window.getSelection()?.removeAllRanges();
    void startEdit(idx);
  }

  function onwindowkey(event: KeyboardEvent) {
    if (event.key !== 'Escape' || editing !== null || selected === null) return;
    if (document.querySelector('dialog[open]')) return;
    selected = null;
  }

  function dirty() {
    if (!editingSeg) return false;
    const original = resolveSpeaker(speakers, editingSeg.speaker);
    return draft.trim() !== editingSeg.text || draftSpeaker !== original || (addTerm && !!termText.trim());
  }

  async function startEdit(idx: number) {
    if (!canEdit || editing === idx) return;
    if (editing !== null && dirty()) {
      const ok = await confirm({
        title: '放弃正在编辑的修改？',
        message: '上一句还没有保存。',
        confirmText: '放弃修改',
        danger: true,
      });
      if (!ok) return;
    }
    const seg = findSegment(idx);
    if (!seg) return;
    editing = idx;
    selected = idx;
    draft = seg.text;
    draftSpeaker = resolveSpeaker(speakers, seg.speaker);
    addTerm = false;
    termText = '';
    termWrong = '';
  }

  function cancelEdit() {
    editing = null;
  }

  async function save() {
    const seg = editingSeg;
    if (!seg || saving) return;
    const text = draft.trim();
    if (!text) {
      toast.error('句子不能为空');
      return;
    }
    const term = termText.trim();
    if (addTerm && !term) {
      toast.error('请填写术语的正确写法');
      return;
    }
    const body: { text?: string; speaker?: string } = {};
    if (text !== seg.text) body.text = text;
    if (draftSpeaker !== resolveSpeaker(speakers, seg.speaker)) body.speaker = draftSpeaker;
    saving = true;
    try {
      if (body.text !== undefined || body.speaker !== undefined) await onedit(seg.idx, body);
      if (addTerm && term) {
        await meetingApi.admin.createTerm({
          term,
          wrong_forms: [...new Set(splitWrongForms(termWrong))].slice(0, MAX_WRONG_FORMS),
          note: `校对会议「${meeting.title}」时添加`.slice(0, 255),
        });
        toast.success(`已把「${term}」加入术语表`);
        addTerm = false;
      }
      editing = null;
    } catch (e) {
      toast.error(e);
    } finally {
      saving = false;
    }
  }

  async function revert(seg: Segment) {
    const ok = await confirm({
      title: '恢复为识别原文？',
      message: '这句的手动修改和大模型整理结果都会换回语音识别的原始文字。',
      confirmText: '恢复原文',
    });
    if (!ok) return;
    saving = true;
    try {
      await onrevert(seg.idx);
      editing = null;
      toast.success('已恢复为识别原文');
    } catch (e) {
      toast.error(e);
    } finally {
      saving = false;
    }
  }

  // 一句话不需要换行（后端也会把换行合成空格），回车直接保存；输入法组字时的回车不算
  function oneditorkey(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault();
      cancelEdit();
    } else if (event.key === 'Enter' && !isImeEnter(event)) {
      event.preventDefault();
      void save();
    }
  }

  function focusEnd(node: HTMLTextAreaElement) {
    requestAnimationFrame(() => {
      node.focus();
      node.setSelectionRange(node.value.length, node.value.length);
    });
  }

  async function polish() {
    const again = transcriptState !== 'raw';
    const ok = await confirm({
      title: again ? '重新整理逐字稿？' : '整理逐字稿？',
      message:
        '大模型会给逐字稿加标点、去掉口头禅和无意义的重复，不添加内容。你手动改过的句子不会被覆盖。整理完成后纪要会标记为过期，需要时再重新生成。',
      confirmText: '开始整理',
    });
    if (!ok) return;
    polishing = true;
    try {
      meetings.upsert(await meetingApi.runOp(meeting.id, 'polish'));
      toast.info('已开始整理，完成后逐字稿会自动刷新');
    } catch (e) {
      toast.error(e);
    } finally {
      polishing = false;
    }
  }
</script>

<svelte:window onkeydown={onwindowkey} onwheel={onuserscroll} ontouchmove={onuserscroll} />

<div class="space-y-4">
  <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
    {#if stateInfo}
      <span
        class="inline-flex h-6 items-center gap-1 rounded-full px-2.5 text-[12px] font-medium whitespace-nowrap {stateInfo.cls}"
        title={stateInfo.title}
      >
        {#if transcriptState === 'polishing'}<LoaderCircle class="size-3.5 animate-spin" />{/if}
        {stateInfo.label}
      </span>
    {/if}
    {#if canPolish}
      <button class="btn btn-ghost btn-sm" disabled={polishing} onclick={polish} title="不会覆盖你手动改过的句子">
        {#if polishing}<LoaderCircle class="size-3.5 animate-spin" />{:else}<WandSparkles class="size-3.5" />{/if}
        {transcriptState === 'raw' ? '整理逐字稿' : '重新整理'}
      </button>
    {/if}
    <div class="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1">
      {#if hasPolished}
        <div class="w-36">
          <Segmented
            bind:value={view}
            size="sm"
            ariaLabel="显示内容"
            options={[
              { value: 'text', label: '整理后' },
              { value: 'raw', label: '原文' },
            ]}
          />
        </div>
      {/if}
      {#if meeting.audio_available}
        <div class="w-28"><Switch bind:checked={follow} label="跟随播放" /></div>
      {/if}
    </div>
  </div>

  <div class="card p-4 sm:p-6">
    {#if loading && !segments.length}
      <div class="flex items-center justify-center gap-2 py-12 text-[13px] text-muted">
        <LoaderCircle class="size-4 animate-spin" /> 正在加载逐字稿…
      </div>
    {:else if !segments.length}
      <div class="flex flex-col items-center py-12 text-center">
        <Mic class="size-9 text-line-strong" strokeWidth={1.5} />
        <p class="mt-3 font-medium">没有识别出说话内容</p>
        <p class="mt-1 text-[13px] text-muted">录音可能是静音，或者音量太小。</p>
      </div>
    {:else}
      <p class="text-[12px] text-muted">
        {#if showRaw}
          正在看语音识别的原文，切回“整理后”才能编辑。
        {:else if canEdit}
          点一句可以从那里播放或编辑，双击直接编辑；改过的句子会标上“已修改”。
        {:else}
          点一句可以从那里播放；处理完成后才能编辑。
        {/if}
      </p>

      <!-- 句子很多，点击和双击统一在外层处理，不给每句挂事件 -->
      <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
      <div bind:this={root} class="mt-2" onclick={onlistclick} ondblclick={onlistdblclick}>
        {#each blocks as block (block.key)}
          <section class="tv-block flex gap-3 py-2.5">
            <span class="w-1 shrink-0 rounded-full {speakerTone(block.speaker)}" aria-hidden="true"></span>
            <div class="min-w-0 flex-1">
              <div class="flex items-baseline gap-2 text-[12.5px]">
                <span class="font-semibold text-ink">{speakerName(speakers, block.speaker)}</span>
                <button class="tabular text-muted hover:text-accent-ink hover:underline" data-seek={block.start} title="从这里播放">
                  {clock(block.start)}
                </button>
              </div>
              <div class="mt-0.5 text-[14.5px] leading-[1.9] break-words text-ink" class:spaced>
                {#if focusIdx !== null && focusIdx >= block.first && focusIdx <= block.last}
                  {@render rich(block)}
                {:else}
                  {@render plain(block)}
                {/if}
              </div>
            </div>
          </section>
        {/each}
      </div>
    {/if}
  </div>
</div>

{#if follow && followPaused && playing && active}
  <button class="btn btn-secondary fixed bottom-6 left-1/2 z-20 -translate-x-1/2 rounded-full shadow-pop" onclick={backToCurrent}>
    <LocateFixed class="size-4 text-accent" /> 回到播放位置
  </button>
{/if}

{#snippet plain(block: Block)}
  {#each block.items as seg (seg.idx)}<span class="sent" data-idx={seg.idx} data-edited={seg.edited ? '' : undefined}
      >{showRaw ? seg.raw_text : seg.text}</span
    >{/each}
{/snippet}

{#snippet rich(block: Block)}
  {#each block.items as seg (seg.idx)}{#if editing === seg.idx}{@render editor(seg)}{:else}<span
        class="sent"
        class:sel={selected === seg.idx}
        data-idx={seg.idx}
        data-edited={seg.edited ? '' : undefined}>{showRaw ? seg.raw_text : seg.text}</span
      >{#if selected === seg.idx}{@render actions(seg)}{/if}{/if}{/each}
{/snippet}

{#snippet actions(seg: Segment)}
  <span class="mx-1 inline-flex items-center gap-0.5 align-[1px] text-[12px] whitespace-nowrap" data-keep>
    {#if meeting.audio_available}
      <button class="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-accent-ink hover:bg-accent-soft" data-seek={seg.start_ms}>
        <Play class="size-3" fill="currentColor" /><span class="tabular">{clock(seg.start_ms)}</span>
      </button>
    {:else}
      <span class="tabular px-1.5 text-muted">{clock(seg.start_ms)}</span>
    {/if}
    {#if canEdit && !showRaw}
      <button
        class="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-ink-2 hover:bg-surface-2 hover:text-ink"
        onclick={() => startEdit(seg.idx)}
      >
        <PenLine class="size-3" />编辑
      </button>
    {/if}
  </span>
{/snippet}

{#snippet editor(seg: Segment)}
  <div class="my-2 rounded-xl border border-accent/40 bg-surface-2/70 p-3 text-[14px] leading-normal" data-keep>
    <div class="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12.5px]">
      <button class="tabular text-muted hover:text-accent-ink hover:underline" data-seek={seg.start_ms} title="从这句开始播放">
        {clock(seg.start_ms)} – {clock(seg.end_ms)}
      </button>
      <label class="flex items-center gap-1.5 text-ink-2">
        说话人
        <select class="field h-8 w-auto py-0 pr-8 text-[13px]" bind:value={draftSpeaker} disabled={saving}>
          {#each speakerOptions as id (id)}<option value={id}>{speakerName(speakers, id)}</option>{/each}
          <option value={NEW_SPEAKER}>新说话人</option>
        </select>
      </label>
      {#if seg.edited}<span class="rounded bg-accent-soft px-1.5 py-px text-[11px] text-accent-ink">已修改</span>{/if}
    </div>
    <textarea
      class="field text-[14px]"
      rows={Math.min(8, Math.max(2, Math.ceil(draft.length / 40)))}
      maxlength={5000}
      bind:value={draft}
      disabled={saving}
      onkeydown={oneditorkey}
      use:focusEnd
      aria-label="句子内容"
    ></textarea>
    {#if seg.raw_text && seg.raw_text !== draft.trim()}
      <p class="mt-1.5 text-[12px] leading-relaxed text-muted"><span class="font-medium">识别原文：</span>{seg.raw_text}</p>
    {/if}

    {#if session.isAdmin}
      <label class="mt-2.5 flex w-fit cursor-pointer items-center gap-2 text-[12.5px] text-ink-2">
        <input type="checkbox" class="size-3.5 accent-accent" bind:checked={addTerm} disabled={saving} />
        同时加入术语表（全站共用，用于以后的整理和热词）
      </label>
      {#if addTerm}
        <div class="mt-2 grid gap-2 sm:grid-cols-2">
          <div>
            <label class="label" for="tv-term">正确写法</label>
            <input id="tv-term" class="field h-9" maxlength={64} placeholder="例如：Transformer" bind:value={termText} disabled={saving} />
          </div>
          <div>
            <label class="label" for="tv-wrong">听错的写法 <span class="font-normal text-muted">（可选，用逗号分隔）</span></label>
            <input id="tv-wrong" class="field h-9" placeholder="例如：穿梭佛么" bind:value={termWrong} disabled={saving} />
          </div>
        </div>
      {/if}
    {/if}

    <div class="mt-3 flex flex-wrap items-center gap-2">
      {#if seg.edited || seg.text !== seg.raw_text}
        <button class="btn btn-ghost btn-sm -ml-2" disabled={saving} onclick={() => revert(seg)}>
          <Undo2 class="size-3.5" /> 恢复识别原文
        </button>
      {/if}
      <span class="ml-auto hidden text-[12px] text-muted sm:inline">回车保存，Esc 取消</span>
      <button class="btn btn-secondary btn-sm max-sm:ml-auto" disabled={saving} onclick={cancelEdit}>取消</button>
      <button class="btn btn-primary btn-sm" disabled={saving} onclick={save}>
        {#if saving}<LoaderCircle class="size-3.5 animate-spin" />{/if}保存
      </button>
    </div>
  </div>
{/snippet}

<style>
  .tv-block {
    content-visibility: auto;
    contain-intrinsic-size: auto 140px;
  }
  .sent {
    border-radius: 4px;
    cursor: pointer;
    transition: background-color 0.15s;
    box-decoration-break: clone;
    -webkit-box-decoration-break: clone;
  }
  .spaced .sent {
    margin-right: 0.3em;
  }
  .sent:hover {
    background: var(--surface-2);
  }
  /* data-current 由脚本直接加在节点上，模板里没有，所以用 :global 防止被当成无用选择器删掉 */
  .sent:global([data-current]) {
    background: var(--accent-soft);
    color: var(--accent-ink);
  }
  .sent.sel {
    background: var(--surface-3);
    box-shadow: inset 0 -2px 0 var(--accent);
  }
  .sent[data-edited]::after {
    content: '已修改';
    margin: 0 0.15em 0 0.25em;
    padding: 0 0.3em;
    border-radius: 4px;
    background: var(--accent-soft);
    color: var(--accent-ink);
    font-size: 10.5px;
    vertical-align: 2px;
    white-space: nowrap;
  }
</style>
