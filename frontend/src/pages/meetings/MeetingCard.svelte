<script lang="ts">
  import ArrowRight from '@lucide/svelte/icons/arrow-right';
  import MeetingStatusBadge from '../../components/MeetingStatusBadge.svelte';
  import Menu from '../../components/Menu.svelte';
  import ProgressBar from '../../components/ProgressBar.svelte';
  import { confirm, type ConfirmOptions } from '../../lib/confirm.svelte';
  import { bytes, compact, dateTime, duration, elapsedSince, expiryLabel, relativeTime } from '../../lib/format';
  import {
    AudioLines,
    Ban,
    ChevronDown,
    CircleAlert,
    LoaderCircle,
    RotateCcw,
    Trash2,
    TriangleAlert,
  } from '../../lib/icons';
  import { meetingApi } from '../../lib/meeting/api';
  import { errorKindLabel, fillSpeakers, isActive, spoken, stageLabel, statusLabel } from '../../lib/meeting/format';
  import type { Meeting, MeetingStatus } from '../../lib/meeting/types';
  import { meetings } from '../../lib/meetings.svelte';
  import { toast } from '../../lib/toast.svelte';
  import { upload } from './uploadTask.svelte';

  let { meeting: m, now }: { meeting: Meeting; now: number } = $props();
  let busy = $state(false);

  const DAY = 86_400_000;
  const tones: Record<MeetingStatus, string> = {
    uploading: 'bg-surface-2 text-ink-2',
    queued: 'bg-surface-2 text-ink-2',
    transcoding: 'bg-accent-soft text-accent',
    transcribing: 'bg-accent-soft text-accent',
    processing: 'bg-accent-soft text-accent',
    done: 'bg-good-soft text-good-ink',
    failed: 'bg-bad-soft text-bad-ink',
    canceled: 'bg-surface-2 text-muted',
  };
  const cancelTexts: Partial<Record<MeetingStatus, Omit<ConfirmOptions, 'danger'>>> = {
    transcoding: {
      title: '取消处理这场会议？',
      message: '音频格式转换会立即停止，之后可以重试。',
      confirmText: '取消处理',
    },
    transcribing: {
      title: '取消识别？',
      message: '已提交给服务商的部分仍会计费，取消后无法退回。之后可以重试，已经识别完的分段会保留。',
      confirmText: '取消识别',
    },
    processing: {
      title: '取消整理？',
      message: '大模型整理会停止，已经识别出的逐字稿会保留，之后可以重试。',
      confirmText: '取消整理',
    },
  };

  const active = $derived(isActive(m));
  const live = $derived(meetings.progressOf(m));
  const uploadingHere = $derived(m.status === 'uploading' && upload.meetingId === m.id);
  const elapsed = $derived(elapsedSince(m.started_at, now));
  const created = $derived.by(() => {
    void now; // 读一下 now，让相对时间跟着每秒的 ticker 刷新
    return relativeTime(m.created_at);
  });
  const templateName = $derived(meetings.options?.templates.find((t) => t.id === m.template)?.name ?? '');
  const speakerCount = $derived(Object.values(m.speakers ?? {}).filter((s) => !s.merged_into).length);
  const partsDone = $derived(m.parts.filter((p) => p.state === 'done').length);
  const stageText = $derived(live.stage ? stageLabel(live.stage) : statusLabel(m.status));
  const errorKind = $derived(errorKindLabel(m.error_kind));
  const expiresIn = $derived(m.audio_expires_at ? new Date(m.audio_expires_at).getTime() - now : null);
  const expiryTitle = $derived(m.audio_expires_at ? `将于 ${dateTime(m.audio_expires_at)} 自动删除，文字记录会保留` : '');
  const expiryText = $derived.by(() => {
    if (expiresIn === null) return '';
    const label = expiryLabel(expiresIn);
    return /^\d/.test(label) ? `录音 ${label}` : `录音${label}`;
  });
  const minutesNote = $derived.by(() => {
    if (m.op === 'minutes' || m.minutes_state === 'generating') return null;
    if (m.minutes_state === 'ready') {
      return m.minutes_stale ? { text: '纪要已过期', cls: 'text-warn-ink' } : { text: '纪要已生成', cls: '' };
    }
    if (m.minutes_state === 'failed') return { text: '纪要生成失败', cls: 'text-bad-ink' };
    return { text: '还没有纪要', cls: '' };
  });
  const transcriptNote = $derived.by(() => {
    if (m.op === 'polish' || m.transcript_state === 'polishing') return '';
    if (m.transcript_state === 'raw') return '逐字稿未整理';
    if (m.transcript_state === 'partial') return '逐字稿部分未整理';
    return '';
  });

  async function run(action: () => Promise<void>) {
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
    const id = m.id;
    const text = cancelTexts[m.status];
    if (text && !(await confirm({ ...text, danger: true }))) return;
    await run(async () => {
      meetings.upsert(await meetingApi.cancel(id));
    });
  }

  async function retry() {
    const id = m.id;
    await run(async () => {
      meetings.upsert(await meetingApi.retry(id));
      toast.success('已重新开始处理');
    });
  }

  async function remove() {
    const id = m.id;
    const ok = await confirm({
      title: '删除这场会议？',
      message:
        m.status === 'uploading'
          ? '这次上传还没有完成，删除后需要重新上传。'
          : `「${m.title}」的录音、逐字稿、纪要和对话记录都会被永久删除。`,
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    await run(async () => {
      await meetingApi.remove(id);
      meetings.remove(id);
      toast.success('已删除');
    });
  }
</script>

<article class="card animate-pop p-4 sm:p-5">
  <div class="flex items-start gap-3.5">
    <div class="grid size-10 shrink-0 place-items-center rounded-xl {tones[m.status] ?? tones.queued}">
      <AudioLines class="size-5" strokeWidth={1.75} />
    </div>

    <div class="min-w-0 flex-1">
      <div class="flex items-start gap-3">
        <h3 class="min-w-0 flex-1 truncate pt-0.5 text-[14.5px] font-semibold" title={m.title}>
          {#if m.status === 'done'}
            <a href="/meetings/{m.id}" class="hover:text-accent hover:underline">{m.title}</a>
          {:else}
            {m.title}
          {/if}
        </h3>
        <MeetingStatusBadge status={m.status} />
      </div>
      <p class="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[12.5px] text-muted">
        <span>{m.duration_ms > 0 ? spoken(m.duration_ms) : bytes(m.file_size)}</span>
        {#if m.provider_name}
          <span aria-hidden="true">·</span>
          <span>{m.provider_name}</span>
        {/if}
        {#if templateName}
          <span aria-hidden="true">·</span>
          <span>{templateName}</span>
        {/if}
        <span aria-hidden="true">·</span>
        <time datetime={m.created_at} title={new Date(m.created_at).toLocaleString('zh-CN')}>{created}</time>
      </p>

      {#if m.status === 'uploading'}
        {#if uploadingHere}
          <div class="mt-3.5">
            <ProgressBar value={upload.ratio * 100} active label="上传进度" />
            <div class="mt-2 flex items-center gap-3 text-[12.5px]">
              <span class="font-medium text-ink">{upload.ratio < 1 ? '上传录音' : '正在提交'}</span>
              <span class="tabular ml-auto font-semibold text-ink">{Math.floor(upload.ratio * 100)}%</span>
            </div>
          </div>
        {:else}
          <p class="mt-3 text-[12.5px] text-ink-2">
            录音还没有传完，可能在别的标签页里上传；中断的上传 24 小时后自动清理。
          </p>
        {/if}
      {:else if m.status === 'queued'}
        <p class="mt-3 text-[12.5px] text-ink-2">排队中，前面的会议处理完就开始</p>
      {:else if active}
        <div class="mt-3.5">
          <ProgressBar value={live.progress} active label="处理进度" />
          <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px]">
            <span class="font-medium text-ink">{stageText}</span>
            {#if m.status === 'transcribing' && m.parts.length > 1}
              <span class="tabular text-muted">已识别 {partsDone}/{m.parts.length} 段</span>
            {/if}
            <span class="ml-auto flex items-center gap-3 text-muted">
              {#if elapsed !== null}<span>已用 {duration(elapsed)}</span>{/if}
              <span class="tabular font-semibold text-ink">{Math.floor(live.progress)}%</span>
            </span>
          </div>
        </div>
      {:else if m.status === 'failed'}
        <div class="mt-3 flex gap-2 rounded-xl bg-bad-soft px-3 py-2.5 text-[12.5px] leading-relaxed text-bad-ink">
          <CircleAlert class="mt-0.5 size-4 shrink-0" />
          <div class="min-w-0 break-words">
            <p class="font-medium">{errorKind || '处理失败'}</p>
            {#if m.error}<p class="mt-0.5 opacity-90">{m.error}</p>{/if}
          </div>
        </div>
      {:else if m.status === 'done'}
        <p class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-muted">
          {#if m.op}
            <span class="inline-flex items-center gap-1 font-medium text-accent-ink">
              <LoaderCircle class="size-3.5 animate-spin" />正在{stageLabel(m.op)}
            </span>
          {/if}
          {#if speakerCount}<span>{speakerCount} 位说话人</span>{/if}
          {#if minutesNote}<span class={minutesNote.cls}>{minutesNote.text}</span>{/if}
          {#if transcriptNote}<span>{transcriptNote}</span>{/if}
          {#if m.tokens}<span>{compact(m.tokens)} tokens</span>{/if}
          {#if expiresIn !== null}
            <span class={expiresIn < 3 * DAY ? 'font-medium text-warn-ink' : ''} title={expiryTitle}>
              {expiryText}
            </span>
          {:else if !m.audio_available}
            <span>录音已清理，文字记录仍保留</span>
          {/if}
        </p>
      {/if}
      {#if m.warning && m.status !== 'failed'}
        <p class="mt-2 flex items-start gap-1.5 rounded-lg bg-warn-soft px-2.5 py-1.5 text-[12.5px] text-warn-ink">
          <TriangleAlert class="mt-0.5 size-3.5 shrink-0" />
          <span class="min-w-0 break-words">{fillSpeakers(m.warning, m.speakers)}</span>
        </p>
      {/if}
      {#if (m.status === 'failed' || m.status === 'canceled') && expiresIn !== null}
        <p class="mt-2 text-[12.5px] {expiresIn < 3 * DAY ? 'text-warn-ink' : 'text-muted'}" title={expiryTitle}>
          {expiryText}，删除前可以重试
        </p>
      {/if}

      <div class="mt-3.5 flex flex-wrap items-center justify-end gap-2">
        {#if m.status === 'done'}
          <a class="btn btn-primary btn-sm" href="/meetings/{m.id}">打开 <ArrowRight class="size-3.5" /></a>
        {/if}
        {#if uploadingHere}
          <button class="btn btn-secondary btn-sm" disabled={upload.canceling || upload.ratio >= 1} onclick={() => upload.cancel()}>
            <Ban class="size-3.5" /> 取消上传
          </button>
        {:else if active && m.status !== 'uploading'}
          <button class="btn btn-secondary btn-sm" disabled={busy} onclick={cancel}>
            <Ban class="size-3.5" /> 取消
          </button>
        {/if}
        {#if m.status === 'failed' || m.status === 'canceled'}
          <button class="btn btn-secondary btn-sm" disabled={busy} onclick={retry}>
            <RotateCcw class="size-3.5" /> 重试
          </button>
        {/if}
        {#if !active || (m.status === 'uploading' && !uploadingHere)}
          <Menu width="w-40">
            {#snippet trigger({ toggle, open })}
              <button class="btn btn-ghost btn-sm" aria-expanded={open} disabled={busy} onclick={toggle}>
                更多 <ChevronDown class="size-3.5" />
              </button>
            {/snippet}
            {#snippet children({ close })}
              <button
                class="menu-item danger"
                onclick={() => {
                  close();
                  void remove();
                }}
              >
                <Trash2 class="size-4" /> 删除会议
              </button>
            {/snippet}
          </Menu>
        {/if}
      </div>
    </div>
  </div>
</article>
