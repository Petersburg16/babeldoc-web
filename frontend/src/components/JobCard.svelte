<script lang="ts">
  import { api, fileUrl } from '../lib/api';
  import { confirm } from '../lib/confirm.svelte';
  import { compact, duration, elapsedSince, expiryLabel, relativeTime, stageLabel } from '../lib/format';
  import {
    Ban,
    BookOpenText,
    ChevronDown,
    CircleAlert,
    Download,
    Eye,
    FileText,
    RotateCcw,
    Trash2,
    TriangleAlert,
  } from '../lib/icons';
  import { jobs } from '../lib/jobs.svelte';
  import { session } from '../lib/session.svelte';
  import { toast } from '../lib/toast.svelte';
  import type { Job } from '../lib/types';
  import Menu from './Menu.svelte';
  import ProgressBar from './ProgressBar.svelte';
  import StatusBadge from './StatusBadge.svelte';

  let { job, now }: { job: Job; now: number } = $props();
  let busy = $state(false);

  const live = $derived(jobs.progressOf(job));
  const elapsed = $derived(elapsedSince(job.started_at, now));
  const eta = $derived.by(() => {
    if (job.status !== 'running' || !elapsed || live.progress < 8 || live.progress >= 99) return null;
    return (elapsed * (100 - live.progress)) / live.progress;
  });
  const DAY = 86_400_000;
  // 与后台清理一致：已结束的任务从结束时刻起算，清理每 30 分钟跑一次，过期未删的显示“即将删除”
  const expiresIn = $derived.by(() => {
    const days = session.meta?.file_retention_days;
    if (!days || job.files_purged || !job.finished_at) return null;
    if (!['succeeded', 'failed', 'canceled'].includes(job.status)) return null;
    return new Date(job.finished_at).getTime() + days * DAY - now;
  });
  const tones = {
    queued: 'bg-surface-2 text-ink-2',
    running: 'bg-accent-soft text-accent',
    succeeded: 'bg-good-soft text-good-ink',
    failed: 'bg-bad-soft text-bad-ink',
    canceled: 'bg-surface-2 text-muted',
  };
  const has = (kind: string) => job.files.includes(kind as never);

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
    if (
      job.status === 'running' &&
      !(await confirm({
        title: '取消这个任务？',
        message: '正在进行的翻译会立即停止。已经翻译过的段落有缓存，之后重试会更快。',
        confirmText: '取消任务',
        danger: true,
      }))
    )
      return;
    await run(async () => {
      jobs.upsert(await api.cancelJob(job.id));
      void session.refreshMe();
    });
  }

  async function retry() {
    await run(async () => {
      jobs.upsert(await api.retryJob(job.id));
      toast.success('已重新加入队列');
      void session.refreshMe();
    });
  }

  async function remove() {
    const ok = await confirm({
      title: '删除这个任务？',
      message: `「${job.filename}」及其译文文件会被永久删除。`,
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    await run(async () => {
      await api.deleteJob(job.id);
      jobs.remove(job.id);
      toast.success('已删除');
    });
  }
</script>

<article class="card animate-pop p-4 sm:p-5">
  <div class="flex items-start gap-3.5">
    <div class="grid size-10 shrink-0 place-items-center rounded-xl {tones[job.status]}">
      <FileText class="size-5" strokeWidth={1.75} />
    </div>

    <div class="min-w-0 flex-1">
      <div class="flex items-start gap-3">
        <h3 class="min-w-0 flex-1 truncate pt-0.5 text-[14.5px] font-semibold" title={job.filename}>{job.filename}</h3>
        <StatusBadge status={job.status} />
      </div>
      <p class="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[12.5px] text-muted">
        <span>{session.languageLabel(job.lang_in)} → {session.languageLabel(job.lang_out)}</span>
        <span aria-hidden="true">·</span>
        <span>{job.billed_pages} 页{job.pages ? `（第 ${job.pages} 页）` : ''}</span>
        {#if job.model_name}
          <span aria-hidden="true">·</span>
          <span>{job.model_name}</span>
        {/if}
        {#if job.options?.term_model_name}
          <span aria-hidden="true">·</span>
          <span>术语 {job.options.term_model_name}</span>
        {/if}
        <span aria-hidden="true">·</span>
        <time datetime={job.created_at} title={new Date(job.created_at).toLocaleString('zh-CN')}>{relativeTime(job.created_at)}</time>
      </p>

      {#if job.status === 'running'}
        <div class="mt-3.5">
          <ProgressBar value={live.progress} active label="翻译进度" />
          <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px]">
            <span class="font-medium text-ink">{stageLabel(live.stage)}</span>
            {#if live.total && live.total > 1}
              <span class="tabular text-muted">{live.current ?? 0}/{live.total}</span>
            {/if}
            {#if live.parts && live.parts > 1}
              <span class="text-muted">第 {live.part}/{live.parts} 部分</span>
            {/if}
            <span class="ml-auto flex items-center gap-3 text-muted">
              {#if elapsed !== null}<span>已用 {duration(elapsed)}</span>{/if}
              {#if eta}<span>约剩 {duration(eta)}</span>{/if}
              <span class="tabular font-semibold text-ink">{Math.floor(live.progress)}%</span>
            </span>
          </div>
        </div>
      {:else if job.status === 'queued'}
        <p class="mt-3 text-[12.5px] text-ink-2">
          {#if job.queue_position === 0}
            即将开始，正在等待空闲的翻译通道
          {:else if job.queue_position}
            排队中，前面还有 <span class="font-semibold text-ink">{job.queue_position}</span> 个任务
          {:else}
            排队中
          {/if}
        </p>
      {:else if job.status === 'failed'}
        <div class="mt-3 flex gap-2 rounded-xl bg-bad-soft px-3 py-2.5 text-[12.5px] leading-relaxed text-bad-ink">
          <CircleAlert class="mt-0.5 size-4 shrink-0" />
          <p class="min-w-0 break-words">
            {job.error ?? '翻译失败'}
            {#if job.error_kind === 'preflight'}
              <span class="opacity-80">（模型接口问题，可以联系管理员检查模型配置）</span>
            {/if}
          </p>
        </div>
      {:else if job.status === 'succeeded'}
        <p class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-muted">
          {#if job.stats?.seconds}<span>用时 {duration(job.stats.seconds)}</span>{/if}
          {#if job.tokens}<span>{compact(job.tokens)} tokens</span>{/if}
          {#if job.files_purged}<span>文件已超过保留期被清理</span>{/if}
          {#if expiresIn !== null}
            <span class={expiresIn < 3 * DAY ? 'font-medium text-warn-ink' : ''} title="原文与译文到期后自动删除，请及时下载">{expiryLabel(expiresIn)}</span>
          {/if}
        </p>
        {#if job.warning}
          <p class="mt-2 flex items-start gap-1.5 rounded-lg bg-warn-soft px-2.5 py-1.5 text-[12.5px] text-warn-ink">
            <TriangleAlert class="mt-0.5 size-3.5 shrink-0" />
            {job.warning}
          </p>
        {/if}
      {/if}
      {#if (job.status === 'failed' || job.status === 'canceled') && expiresIn !== null}
        <p class="mt-2 text-[12.5px] {expiresIn < 3 * DAY ? 'text-warn-ink' : 'text-muted'}">原文 {expiryLabel(expiresIn)}，删除前可以重试</p>
      {/if}

      <div class="mt-3.5 flex flex-wrap items-center justify-end gap-2">
        {#if job.status === 'succeeded' && !job.files_purged}
          {#if has('dual')}
            <a class="btn btn-primary btn-sm" href={fileUrl(job, 'dual')} download>
              <Download class="size-3.5" /> 双语对照
            </a>
          {/if}
          {#if has('mono')}
            <a class="btn btn-secondary btn-sm" href={fileUrl(job, 'mono')} download>
              <Download class="size-3.5" /> 仅译文
            </a>
          {/if}
        {/if}
        {#if job.status === 'running' || job.status === 'queued'}
          <button class="btn btn-secondary btn-sm" disabled={busy} onclick={cancel}>
            <Ban class="size-3.5" /> 取消
          </button>
        {/if}
        {#if (job.status === 'failed' || job.status === 'canceled') && !job.files_purged}
          <button class="btn btn-secondary btn-sm" disabled={busy} onclick={retry}>
            <RotateCcw class="size-3.5" /> 重试
          </button>
        {/if}
        {#if job.status !== 'running' && job.status !== 'queued'}
          <Menu width="w-44">
            {#snippet trigger({ toggle, open })}
              <button class="btn btn-ghost btn-sm" aria-expanded={open} onclick={toggle}>
                更多 <ChevronDown class="size-3.5" />
              </button>
            {/snippet}
            {#snippet children({ close })}
              {#if has('dual')}
                <a class="menu-item" href={fileUrl(job, 'dual', true)} target="_blank" rel="noopener" onclick={close}>
                  <Eye class="size-4 text-muted" /> 在线预览双语
                </a>
              {/if}
              {#if has('mono')}
                <a class="menu-item" href={fileUrl(job, 'mono', true)} target="_blank" rel="noopener" onclick={close}>
                  <Eye class="size-4 text-muted" /> 在线预览译文
                </a>
              {/if}
              {#if has('glossary')}
                <a class="menu-item" href={fileUrl(job, 'glossary')} download onclick={close}>
                  <BookOpenText class="size-4 text-muted" /> 下载术语表
                </a>
              {/if}
              {#if has('original')}
                <a class="menu-item" href={fileUrl(job, 'original')} download onclick={close}>
                  <FileText class="size-4 text-muted" /> 下载原文
                </a>
              {/if}
              <div class="menu-sep"></div>
              <button
                class="menu-item danger"
                onclick={() => {
                  close();
                  void remove();
                }}
              >
                <Trash2 class="size-4" /> 删除任务
              </button>
            {/snippet}
          </Menu>
        {/if}
      </div>
    </div>
  </div>
</article>
