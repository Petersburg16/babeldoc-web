<script lang="ts">
  import { onDestroy, onMount, untrack } from 'svelte';
  import MeetingStatusBadge from '../../components/MeetingStatusBadge.svelte';
  import ExportMenu from './ExportMenu.svelte';
  import { ArrowLeft } from '../../lib/meeting/icons';
  import ProgressBar from '../../components/ProgressBar.svelte';
  import Segmented from '../../components/Segmented.svelte';
  import { ApiError } from '../../lib/api';
  import { confirm } from '../../lib/confirm.svelte';
  import { DAY, dateTime, duration, elapsedSince } from '../../lib/format';
  import { Ban, CircleAlert, Clock, Compass, LoaderCircle, Pencil, RotateCcw, Trash2, TriangleAlert } from '../../lib/icons';
  import { retryMeeting } from '../../lib/meeting/actions';
  import { audioUrl, meetingApi } from '../../lib/meeting/api';
  import {
    audioExpiryText,
    errorKindLabel,
    fillSpeakers,
    isActive,
    meetingStageLabel,
    resolveSpeaker,
    spoken,
    statusLabel,
  } from '../../lib/meeting/format';
  import type { Segment } from '../../lib/meeting/types';
  import { meetings } from '../../lib/meetings.svelte';
  import { router } from '../../lib/router.svelte';
  import { session } from '../../lib/session.svelte';
  import { toast } from '../../lib/toast.svelte';
  import AudioPlayer from './AudioPlayer.svelte';
  import ChatPanel from './ChatPanel.svelte';
  import MinutesView from './MinutesView.svelte';
  import SpeakerBar from './SpeakerBar.svelte';
  import TranscriptView from './TranscriptView.svelte';
  import { upload } from './uploadTask.svelte';

  let { id }: { id: string } = $props();

  type Tab = 'minutes' | 'transcript' | 'chat';
  const TABS: { value: Tab; label: string }[] = [
    { value: 'minutes', label: '纪要' },
    { value: 'transcript', label: '逐字稿' },
    { value: 'chat', label: '对话' },
  ];

  let notFound = $state(false);
  let everLoaded = $state(false);
  let loadError = $state('');
  let busy = $state(false);
  let editingTitle = $state(false);
  let titleDraft = $state('');
  let now = $state(Date.now());

  let segments = $state.raw<Segment[]>([]);
  let segmentsLoading = $state(false);
  let segmentsError = $state('');
  // 已拉到的逐字稿版本；不是响应式状态，只用来判断要不要重新拉
  let segmentsRev = -1;
  let segmentsSeq = 0;

  let player = $state<ReturnType<typeof AudioPlayer>>();
  let currentMs = $state(0);
  let playing = $state(false);
  let visited = $state<Record<Tab, boolean>>({ minutes: false, transcript: false, chat: false });

  const meeting = $derived(meetings.detail?.id === id ? meetings.detail : null);
  const live = $derived(meeting ? meetings.progressOf(meeting) : null);
  const hasContent = $derived(!!meeting && meeting.transcript_rev > 0);
  const urlTab = $derived(router.query.get('tab'));
  const tab = $derived<Tab>(isTab(urlTab) ? urlTab : 'transcript');
  const speakerCount = $derived.by(() => {
    if (!meeting) return 0;
    const map = meeting.speakers ?? {};
    return Object.keys(map).filter((sid) => resolveSpeaker(map, sid) === sid).length;
  });
  // 与会议卡片一致：按秒计、用 duration 显示
  const elapsed = $derived(elapsedSince(meeting?.started_at, now));
  const audioLeft = $derived(
    meeting?.audio_available && meeting.audio_expires_at ? new Date(meeting.audio_expires_at).getTime() - now : null,
  );
  const audioLabel = $derived(audioLeft === null ? '' : audioExpiryText(audioLeft));
  const doneParts = $derived(meeting ? meeting.parts.filter((p) => p.state === 'done').length : 0);

  // 不用 document.title 取站名：换会议时新旧页面的创建和销毁顺序不保证，可能取到上一场会议的标题
  const siteTitle = $derived(session.siteName);

  function isTab(value: string | null): value is Tab {
    return value === 'minutes' || value === 'transcript' || value === 'chat';
  }

  async function open() {
    notFound = false;
    loadError = '';
    try {
      await meetings.openDetail(id);
      everLoaded = true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) notFound = true;
      else loadError = e instanceof Error ? e.message : String(e);
    }
  }

  onMount(() => {
    void open();
  });

  onDestroy(() => {
    if (meetings.detail?.id === id) meetings.closeDetail();
    document.title = siteTitle;
  });

  $effect(() => {
    document.title = meeting ? `${meeting.title} · ${siteTitle}` : siteTitle;
  });

  // 处理中每秒刷新一次“已用时间”和录音保留期
  $effect(() => {
    if (!meeting || (!isActive(meeting) && !meeting.audio_expires_at)) return;
    const timer = setInterval(() => (now = Date.now()), isActive(meeting) ? 1000 : 60_000);
    return () => clearInterval(timer);
  });

  // ---------- 逐字稿：transcript_rev 变了就重新拉 ----------

  $effect(() => {
    const rev = meeting?.transcript_rev ?? 0;
    if (rev <= 0) return;
    untrack(() => {
      if (rev !== segmentsRev) void loadSegments(rev);
    });
  });

  async function loadSegments(rev: number) {
    const seq = ++segmentsSeq;
    segmentsLoading = true;
    try {
      const list = await meetingApi.segments(id);
      if (seq !== segmentsSeq) return;
      segments = list;
      segmentsRev = rev;
      segmentsError = '';
    } catch (e) {
      if (seq === segmentsSeq) segmentsError = e instanceof Error ? e.message : String(e);
    } finally {
      if (seq === segmentsSeq) segmentsLoading = false;
    }
  }

  function reloadSegments() {
    if (meeting && meeting.transcript_rev > 0) void loadSegments(meeting.transcript_rev);
  }

  function replaceSegment(seg: Segment) {
    const index = segments.findIndex((s) => s.idx === seg.idx);
    if (index < 0) return;
    const next = segments.slice();
    next[index] = seg;
    segments = next;
  }

  // 先把返回的句子换进去让界面立即更新；随后 transcript_rev 的事件会触发一次完整重拉兜底
  async function editSegment(idx: number, body: { text?: string; speaker?: string }) {
    replaceSegment(await meetingApi.editSegment(id, idx, body));
  }

  async function revertSegment(idx: number) {
    replaceSegment(await meetingApi.revertSegment(id, idx));
  }

  // ---------- 标签：记在地址的 ?tab= 里 ----------

  function setTab(next: Tab) {
    const params = new URLSearchParams(location.search);
    if (params.get('tab') === next) return;
    params.set('tab', next);
    // 不用 router.go：它会滚回页面顶部
    history.replaceState(history.state, '', `${location.pathname}?${params}`);
    router.sync();
  }

  // 第一次有内容时把默认标签写进地址，之后纪要生成完也不会自动跳走
  let tabChosen = false;
  $effect(() => {
    if (!meeting || !hasContent || tabChosen) return;
    tabChosen = true;
    const fallback: Tab = meeting.minutes_md ? 'minutes' : 'transcript';
    untrack(() => {
      if (!isTab(router.query.get('tab'))) setTab(fallback);
    });
  });

  $effect(() => {
    const current = tab;
    untrack(() => {
      if (!visited[current]) visited[current] = true;
    });
  });

  function seek(ms: number) {
    player?.seek(ms);
  }

  // ---------- 头部操作 ----------

  function startTitle() {
    if (!meeting) return;
    titleDraft = meeting.title;
    editingTitle = true;
  }

  async function saveTitle() {
    if (!editingTitle || !meeting) return;
    editingTitle = false;
    const title = titleDraft.trim().slice(0, 200);
    if (!title || title === meeting.title) return;
    try {
      meetings.upsert(await meetingApi.patch(id, { title }));
    } catch (e) {
      toast.error(e);
    }
  }

  function ontitlekey(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault();
      editingTitle = false;
    } else if (event.key === 'Enter' && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      void saveTitle();
    }
  }

  function focusSelect(node: HTMLInputElement) {
    node.focus();
    node.select();
  }

  async function act(action: () => Promise<void>) {
    busy = true;
    try {
      await action();
    } catch (e) {
      toast.error(e);
    } finally {
      busy = false;
    }
  }

  async function cancel() {
    if (!meeting) return;
    const message =
      meeting.status === 'transcribing'
        ? '已经提交给识别服务的部分仍会计费。取消后可以重试，已识别完的部分不会重复提交。'
        : meeting.status === 'processing'
          ? '大模型整理会停止，已识别出的逐字稿会保留。取消后可以重试。'
          : '取消后可以重试。';
    const ok = await confirm({ title: '取消处理这场会议？', message, confirmText: '取消处理', danger: true });
    if (!ok) return;
    await act(async () => {
      meetings.upsert(await meetingApi.cancel(id));
    });
  }

  async function retry() {
    await act(() => retryMeeting(id));
  }

  async function remove() {
    if (!meeting) return;
    const charged = meeting.status === 'transcribing' ? '已经提交给识别服务的部分仍会计费。' : '';
    const ok = await confirm({
      title: '删除这场会议？',
      message: `「${meeting.title}」的录音、逐字稿、纪要和对话都会被永久删除。${charged}`,
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    await act(async () => {
      await meetingApi.remove(id);
      meetings.remove(id);
      toast.success('已删除');
      router.go('/meetings');
    });
  }
</script>

<div class="mx-auto max-w-4xl">
  <a href="/meetings" class="inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-ink">
    <ArrowLeft class="size-3.5" /> 全部会议
  </a>

  {#if notFound || (everLoaded && !meeting)}
    <div class="flex flex-col items-center py-24 text-center">
      <Compass class="size-10 text-line-strong" strokeWidth={1.5} />
      <p class="mt-4 font-medium">会议不存在</p>
      <p class="mt-1 text-[13px] text-muted">可能已经被删除，或者链接有误。</p>
      <a href="/meetings" class="btn btn-secondary mt-6">回到会议列表</a>
    </div>
  {:else if loadError}
    <div class="flex flex-col items-center py-24 text-center">
      <CircleAlert class="size-10 text-line-strong" strokeWidth={1.5} />
      <p class="mt-4 font-medium">会议加载失败</p>
      <p class="mt-1 text-[13px] text-muted">{loadError}</p>
      <button class="btn btn-secondary mt-6" onclick={open}>重试</button>
    </div>
  {:else if !meeting}
    <div class="flex items-center justify-center gap-2 py-24 text-[13px] text-muted">
      <LoaderCircle class="size-4 animate-spin" /> 正在加载…
    </div>
  {:else}
    <header class="mt-3 flex items-start gap-3">
      <div class="min-w-0 flex-1">
        {#if editingTitle}
          <input
            class="field h-10 text-[18px] font-semibold"
            maxlength={200}
            bind:value={titleDraft}
            onblur={saveTitle}
            onkeydown={ontitlekey}
            use:focusSelect
            aria-label="会议标题"
          />
        {:else}
          <h1 class="text-[20px] leading-snug font-semibold tracking-tight sm:text-[22px]">
            <button class="group text-left break-words hover:text-accent-ink" onclick={startTitle} title="点击修改标题">
              {meeting.title}
              <Pencil class="ml-1 inline size-3.5 align-[2px] text-muted opacity-0 transition-opacity group-hover:opacity-100 max-sm:opacity-60" />
            </button>
          </h1>
        {/if}
        <p class="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[12.5px] text-muted">
          <time datetime={meeting.created_at} title={new Date(meeting.created_at).toLocaleString('zh-CN')}>
            {dateTime(meeting.created_at)}
          </time>
          {#if meeting.duration_ms > 0}
            <span aria-hidden="true">·</span><span>{spoken(meeting.duration_ms)}</span>
          {/if}
          {#if meeting.provider_name}
            <span aria-hidden="true">·</span><span title="语音识别服务">识别 {meeting.provider_name}</span>
          {/if}
          <!-- 老会议（llm_preset_id 为空）的 model_name 是当时的翻译模型名，不显示 -->
          {#if meeting.llm_preset_id != null && meeting.model_name}
            <span aria-hidden="true">·</span><span title="整理逐字稿、生成纪要和对话用的整理方案">方案 {meeting.model_name}</span>
          {/if}
          {#if speakerCount}
            <span aria-hidden="true">·</span><span>{speakerCount} 位说话人</span>
          {/if}
          {#if audioLeft !== null}
            <span aria-hidden="true">·</span>
            <span
              class={audioLeft < 3 * DAY ? 'font-medium text-warn-ink' : ''}
              title="录音 {dateTime(meeting.audio_expires_at)} 删除，逐字稿和纪要会一直保留"
            >
              {audioLabel}
            </span>
          {/if}
        </p>
      </div>

      <div class="flex shrink-0 items-center gap-1 pt-1">
        <MeetingStatusBadge status={meeting.status} />
        {#if meeting.transcript_state !== 'none'}<ExportMenu {meeting} />{/if}
        <button class="btn btn-ghost btn-sm btn-icon" disabled={busy} onclick={remove} title="删除会议" aria-label="删除会议">
          <Trash2 class="size-4" />
        </button>
      </div>
    </header>

    {#if isActive(meeting)}
      <section class="card mt-5 p-4 sm:p-5">
        {#if meeting.status === 'uploading' && upload.active && upload.meetingId === id}
          <ProgressBar value={upload.ratio * 100} active label="上传进度" />
          <div class="mt-2 flex items-center gap-3 text-[12.5px]">
            <span class="font-medium text-ink">正在上传录音</span>
            <span class="tabular ml-auto font-semibold text-ink">{Math.floor(upload.ratio * 100)}%</span>
          </div>
          <div class="mt-3 flex justify-end">
            <button
              class="btn btn-secondary btn-sm"
              disabled={upload.canceling || upload.ratio >= 1}
              onclick={() => upload.cancel()}
            >
              <Ban class="size-3.5" /> 取消上传
            </button>
          </div>
        {:else if meeting.status === 'uploading'}
          <p class="text-[13.5px] text-ink-2">录音还没有传完。没传完的记录会在 24 小时后自动清理，也可以直接删除。</p>
        {:else if meeting.status === 'queued'}
          <p class="flex items-center gap-2 text-[13.5px] text-ink-2">
            <Clock class="size-4 text-muted" /> 排队中，前面的会议处理完就开始
          </p>
        {:else if live}
          <ProgressBar value={live.progress} active label="处理进度" />
          <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px]">
            <span class="font-medium text-ink">{live.stage ? meetingStageLabel(live.stage) : statusLabel(meeting.status)}</span>
            {#if meeting.status === 'transcribing' && meeting.parts.length > 1}
              <span class="text-muted">已完成 {doneParts}/{meeting.parts.length} 段</span>
            {/if}
            <span class="ml-auto flex items-center gap-3 text-muted">
              {#if elapsed !== null && elapsed > 0}<span>已用 {duration(elapsed)}</span>{/if}
              <span class="tabular font-semibold text-ink">{Math.floor(live.progress)}%</span>
            </span>
          </div>
          <p class="mt-2.5 text-[12.5px] text-muted">
            {#if meeting.status === 'transcribing'}
              录音已交给 {meeting.provider_name || '识别服务'}，可以先离开这个页面，处理完会自动更新。
            {:else}
              可以先离开这个页面，处理完会自动更新。
            {/if}
          </p>
        {/if}
        {#if meeting.status !== 'uploading'}
          <div class="mt-3 flex justify-end">
            <button class="btn btn-secondary btn-sm" disabled={busy} onclick={cancel}>
              <Ban class="size-3.5" /> 取消
            </button>
          </div>
        {/if}
      </section>
    {:else if meeting.status === 'failed'}
      <div class="mt-5 flex flex-wrap items-start gap-x-3 gap-y-2 rounded-xl bg-bad-soft px-3.5 py-3 text-[13px] text-bad-ink">
        <CircleAlert class="mt-0.5 size-4 shrink-0" />
        <div class="min-w-0 flex-1">
          <p class="font-medium">{errorKindLabel(meeting.error_kind) || '处理失败'}</p>
          {#if meeting.error}<p class="mt-0.5 break-words opacity-90">{meeting.error}</p>{/if}
        </div>
        <button class="btn btn-secondary btn-sm ml-auto" disabled={busy} onclick={retry}>
          <RotateCcw class="size-3.5" /> 重试
        </button>
      </div>
    {:else if meeting.status === 'canceled'}
      <div class="mt-5 flex flex-wrap items-center gap-3 rounded-xl bg-surface-2 px-3.5 py-3 text-[13px] text-ink-2">
        <Ban class="size-4 shrink-0 text-muted" />
        <p class="min-w-0 flex-1">已取消处理{hasContent ? '，已识别出的逐字稿保留在下面' : ''}。</p>
        <button class="btn btn-secondary btn-sm" disabled={busy} onclick={retry}>
          <RotateCcw class="size-3.5" /> 重试
        </button>
      </div>
    {/if}

    {#if meeting.warning}
      <p class="mt-3 flex items-start gap-2 rounded-xl bg-warn-soft px-3.5 py-2.5 text-[12.5px] leading-relaxed whitespace-pre-line text-warn-ink">
        <TriangleAlert class="mt-0.5 size-3.5 shrink-0" />
        <span class="min-w-0 break-words">{fillSpeakers(meeting.warning, meeting.speakers)}</span>
      </p>
    {/if}

    {#if hasContent}
      <div class="mt-4 pb-3 {meeting.audio_available ? 'sticky top-14 z-20 bg-page pt-2' : ''}">
        <AudioPlayer
          bind:this={player}
          bind:currentMs
          bind:playing
          src={audioUrl(id)}
          available={meeting.audio_available}
          durationMs={meeting.duration_ms}
        />
      </div>

      <SpeakerBar {meeting} {segments} onseek={seek} onrefresh={reloadSegments} />

      <div class="mt-5 max-w-xs">
        <Segmented bind:value={() => tab, (value) => setTab(value)} options={TABS} ariaLabel="会议内容" />
      </div>

      <div class="mt-4 pb-6">
        {#if visited.minutes}
          <div hidden={tab !== 'minutes'}><MinutesView {meeting} onseek={seek} /></div>
        {/if}
        {#if visited.transcript}
          <div hidden={tab !== 'transcript'}>
            {#if segmentsError && !segments.length}
              <div class="card flex flex-col items-center py-14 text-center">
                <CircleAlert class="size-9 text-line-strong" strokeWidth={1.5} />
                <p class="mt-3 font-medium">逐字稿加载失败</p>
                <p class="mt-1 text-[13px] text-muted">{segmentsError}</p>
                <button class="btn btn-secondary mt-5" onclick={() => loadSegments(meeting.transcript_rev)}>重试</button>
              </div>
            {:else}
              <TranscriptView
                {meeting}
                {segments}
                loading={segmentsLoading}
                {currentMs}
                {playing}
                active={tab === 'transcript'}
                onseek={seek}
                onedit={editSegment}
                onrevert={revertSegment}
              />
            {/if}
          </div>
        {/if}
        {#if visited.chat}
          <div hidden={tab !== 'chat'}><ChatPanel {meeting} onseek={seek} /></div>
        {/if}
      </div>
    {/if}
  {/if}
</div>
