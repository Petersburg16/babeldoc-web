<script lang="ts">
  import { onMount } from 'svelte';
  import ProgressBar from '../../components/ProgressBar.svelte';
  import { copyText, dateTime, relativeTime } from '../../lib/format';
  import { CircleAlert, Copy, LoaderCircle, RefreshCw, Sparkles, TriangleAlert } from '../../lib/icons';
  import { meetingApi } from '../../lib/meeting/api';
  import { fillSpeakers, isActive, stageLabel } from '../../lib/meeting/format';
  import { NotebookPen } from '../../lib/meeting/icons';
  import { withSpeakerNames } from '../../lib/meeting/markdown';
  import type { MeetingDetail, MeetingOp } from '../../lib/meeting/types';
  import { meetings } from '../../lib/meetings.svelte';
  import { toast } from '../../lib/toast.svelte';
  import MeetingMarkdown from './MeetingMarkdown.svelte';
  import RegenerateModal from './RegenerateModal.svelte';

  let { meeting, onseek }: { meeting: MeetingDetail; onseek: (ms: number) => void } = $props();

  let modalOpen = $state(false);
  let starting = $state(false);

  const OP_LABELS: Record<MeetingOp, string> = {
    speakers: '正在识别说话人…',
    polish: '正在整理逐字稿…',
    minutes: '正在生成纪要…',
  };

  const templates = $derived(meetings.options?.templates ?? []);
  const templateName = $derived.by(() => {
    const id = meeting.minutes_template ?? meeting.template;
    return templates.find((t) => t.id === id)?.name ?? '';
  });
  const live = $derived(meetings.progressOf(meeting));
  // 后端 set_progress 把大模型步骤的 0–1 映射到总进度的 60–100，这里换回本步骤自己的 0–100
  const LLM_FROM = 60;
  const stepProgress = $derived(Math.max(0, Math.min(100, ((live.progress - LLM_FROM) / (100 - LLM_FROM)) * 100)));
  const hasTranscript = $derived(meeting.transcript_state !== 'none');
  const generating = $derived(meeting.minutes_state === 'generating' || meeting.op === 'minutes');
  // 识别、整理还没跑完：纪要会由后台流水线自动生成，不需要用户点
  const pipeline = $derived(isActive(meeting));
  const busy = $derived(starting || meeting.op !== null || pipeline);
  // 生成纪要时下面有进度卡片，工具栏就不重复提示了
  const busyText = $derived(generating ? '' : meeting.op ? OP_LABELS[meeting.op] : pipeline ? '会议还在处理中…' : '');

  onMount(() => {
    if (!meetings.options) void meetings.loadOptions().catch((e) => toast.error(e));
  });

  async function generate() {
    if (busy) return;
    starting = true;
    try {
      meetings.upsert(await meetingApi.runOp(meeting.id, 'minutes'));
    } catch (e) {
      toast.error(e);
    } finally {
      starting = false;
    }
  }

  async function copy() {
    if (!meeting.minutes_md) return;
    if (await copyText(withSpeakerNames(meeting.minutes_md, meeting.speakers))) toast.success('纪要已复制');
    else toast.error('复制失败，请手动选择文字复制');
  }
</script>

<div class="space-y-4">
  {#if hasTranscript}
    <div class="flex flex-wrap items-center gap-2">
      <div class="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted">
        {#if meeting.minutes_md && templateName}
          <span class="inline-flex items-center gap-1.5 rounded-md bg-surface-2 px-2 py-0.5 font-medium text-ink-2">
            <NotebookPen class="size-3.5" />{templateName}
          </span>
        {/if}
        {#if meeting.minutes_md && meeting.minutes_at}
          <time datetime={meeting.minutes_at} title={dateTime(meeting.minutes_at)}>生成于 {relativeTime(meeting.minutes_at)}</time>
        {/if}
        {#if busyText}
          <span class="inline-flex items-center gap-1.5 text-ink-2">
            <LoaderCircle class="size-3.5 animate-spin" />{busyText}
          </span>
        {/if}
      </div>
      <div class="flex flex-wrap items-center gap-2">
        {#if meeting.minutes_md}
          <button class="btn btn-ghost btn-sm" disabled={busy} onclick={() => (modalOpen = true)}>
            <RefreshCw class="size-3.5" /> 重新生成
          </button>
          <button class="btn btn-ghost btn-sm" onclick={copy}>
            <Copy class="size-3.5" /> 复制
          </button>
        {/if}
      </div>
    </div>
  {/if}

  {#if meeting.minutes_stale && meeting.minutes_md && !generating}
    <div class="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl bg-warn-soft px-3.5 py-2.5 text-[13px] text-warn-ink">
      <TriangleAlert class="size-4 shrink-0" />
      <p class="min-w-0 flex-1">逐字稿改过了，纪要可能不是最新的。</p>
      <button class="btn btn-secondary btn-sm" disabled={busy} onclick={() => (modalOpen = true)}>
        <RefreshCw class="size-3.5" /> 重新生成
      </button>
    </div>
  {/if}

  {#if generating}
    <div class="card p-4 sm:p-5">
      <div class="flex items-center gap-2 text-[13.5px] font-medium">
        <Sparkles class="size-4 text-accent" />
        {meeting.minutes_md ? '正在重新生成纪要' : '正在生成纪要'}
      </div>
      <div class="mt-3">
        <ProgressBar value={stepProgress} active label="纪要生成进度" />
      </div>
      <p class="mt-2 text-[12.5px] text-muted">
        {live.stage ? stageLabel(live.stage) : '准备中'}，长会议可能需要几分钟，可以先去看逐字稿或离开页面。
      </p>
    </div>
  {/if}

  {#if meeting.minutes_state === 'failed' && !generating}
    <!-- 大模型步骤的错误写在 warning 里（error 只给识别主流程用） -->
    <div class="card p-4 sm:p-5">
      <div class="flex gap-2 rounded-xl bg-bad-soft px-3 py-2.5 text-[13px] leading-relaxed text-bad-ink">
        <CircleAlert class="mt-0.5 size-4 shrink-0" />
        <div class="min-w-0 break-words">
          <p class="font-medium">{meeting.minutes_md ? '纪要重新生成失败，下面仍是之前的版本' : '纪要生成失败'}</p>
          {#if meeting.warning}
            <p class="mt-0.5 whitespace-pre-line opacity-90">{fillSpeakers(meeting.warning, meeting.speakers)}</p>
          {/if}
        </div>
      </div>
      <div class="mt-3 flex flex-wrap justify-end gap-2">
        <button class="btn btn-secondary btn-sm" disabled={busy} onclick={() => (modalOpen = true)}>换模板或方案</button>
        <button class="btn btn-primary btn-sm" disabled={busy} onclick={generate}>
          {#if starting}<LoaderCircle class="size-3.5 animate-spin" />{:else}<RefreshCw class="size-3.5" />{/if} 重试
        </button>
      </div>
    </div>
  {/if}

  {#if meeting.minutes_md}
    <article class="card p-4 transition-opacity sm:p-6 {generating ? 'opacity-55' : ''}">
      <MeetingMarkdown source={meeting.minutes_md} speakers={meeting.speakers} durationMs={meeting.duration_ms} {onseek} />
    </article>
  {:else if generating || meeting.minutes_state === 'failed'}
    <!-- 上面的进度卡片或错误卡片已经说明了，这里不再放空状态 -->
  {:else if pipeline}
    <div class="card flex flex-col items-center px-6 py-12 text-center">
      <div class="grid size-11 place-items-center rounded-2xl bg-accent-soft text-accent">
        <NotebookPen class="size-5" strokeWidth={1.75} />
      </div>
      <p class="mt-3 text-[14px] font-medium">纪要会在识别和整理完成后自动生成</p>
      <p class="mt-1 text-[12.5px] text-muted">不用守着页面，处理完会自动显示在这里。</p>
    </div>
  {:else if !hasTranscript}
    <div class="card flex flex-col items-center px-6 py-12 text-center">
      <div class="grid size-11 place-items-center rounded-2xl bg-surface-2 text-muted">
        <NotebookPen class="size-5" strokeWidth={1.75} />
      </div>
      <p class="mt-3 text-[14px] font-medium">没有逐字稿，无法生成纪要</p>
      <p class="mt-1 text-[12.5px] text-muted">这场会议没有识别出内容，可以重试识别后再生成。</p>
    </div>
  {:else}
    <div class="card flex flex-col items-center px-6 py-12 text-center">
      <div class="grid size-11 place-items-center rounded-2xl bg-accent-soft text-accent">
        <NotebookPen class="size-5" strokeWidth={1.75} />
      </div>
      <p class="mt-3 text-[14px] font-medium">还没有纪要</p>
      <p class="mt-1 max-w-sm text-[12.5px] text-muted">
        用大模型按{templateName ? `「${templateName}」` : '所选'}模板整理议题、结论和待办，每条都带时间戳，点击可以跳到录音。
      </p>
      <div class="mt-5 flex flex-wrap justify-center gap-2">
        <button class="btn btn-secondary" disabled={busy} onclick={() => (modalOpen = true)}>选择模板和方案</button>
        <button class="btn btn-primary" disabled={busy} onclick={generate}>
          {#if starting}<LoaderCircle class="size-4 animate-spin" />{:else}<Sparkles class="size-4" />{/if} 生成纪要
        </button>
      </div>
    </div>
  {/if}
</div>

<RegenerateModal open={modalOpen} {meeting} {templates} onclose={() => (modalOpen = false)} />
