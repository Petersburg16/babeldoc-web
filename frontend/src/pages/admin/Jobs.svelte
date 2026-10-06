<script lang="ts">
  import { onDestroy, onMount, untrack } from 'svelte';
  import Modal from '../../components/Modal.svelte';
  import Segmented from '../../components/Segmented.svelte';
  import StatusBadge from '../../components/StatusBadge.svelte';
  import { api } from '../../lib/api';
  import { confirm } from '../../lib/confirm.svelte';
  import { bytes, compact, dateTime, duration, jobStageLabel } from '../../lib/format';
  import { Ban, LoaderCircle, RefreshCw, RotateCcw, ScrollText, Search, Trash2 } from '../../lib/icons';
  import { session } from '../../lib/session.svelte';
  import { toast } from '../../lib/toast.svelte';
  import type { Job } from '../../lib/types';

  const PAGE = 50;
  let status = $state<'all' | 'active' | 'succeeded' | 'failed'>('all');
  let query = $state('');
  let items = $state<Job[]>([]);
  let total = $state(0);
  let loading = $state(false);
  let logJob = $state<Job | null>(null);
  let logText = $state('');
  let logLoading = $state(false);
  let timer: ReturnType<typeof setInterval> | undefined;
  let searchDelay: ReturnType<typeof setTimeout> | undefined;

  async function load(more = false) {
    loading = true;
    try {
      const page = await api.admin.jobs({ status, q: query.trim(), limit: PAGE, offset: more ? items.length : 0 });
      items = more ? [...items, ...page.items] : page.items;
      total = page.total;
    } catch (e) {
      toast.error(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    void status;
    untrack(() => void load());
  });

  function onSearch() {
    clearTimeout(searchDelay);
    searchDelay = setTimeout(() => load(), 300);
  }

  onMount(() => {
    timer = setInterval(() => {
      if (document.visibilityState === 'visible' && items.some((j) => j.status === 'running' || j.status === 'queued'))
        void load();
    }, 4000);
  });

  onDestroy(() => {
    clearInterval(timer);
    clearTimeout(searchDelay);
  });

  async function act(job: Job, action: 'cancel' | 'retry' | 'delete') {
    if (action === 'delete') {
      const ok = await confirm({
        title: '删除任务？',
        message: `「${job.filename}」（@${job.username}）的所有文件会被删除。`,
        confirmText: '删除',
        danger: true,
      });
      if (!ok) return;
    }
    try {
      if (action === 'cancel') await api.admin.cancelJob(job.id);
      if (action === 'retry') await api.admin.retryJob(job.id);
      if (action === 'delete') await api.admin.deleteJob(job.id);
      toast.success({ cancel: '已取消', retry: '已重新排队', delete: '已删除' }[action]);
      await load();
    } catch (e) {
      toast.error(e);
    }
  }

  async function openLog(job: Job) {
    logJob = job;
    logText = '';
    logLoading = true;
    try {
      logText = (await api.admin.jobLog(job.id)).log || '（没有日志）';
    } catch (e) {
      logText = e instanceof Error ? e.message : String(e);
    } finally {
      logLoading = false;
    }
  }

  function runtime(job: Job) {
    if (!job.started_at) return '—';
    const end = job.finished_at ? new Date(job.finished_at).getTime() : Date.now();
    return duration((end - new Date(job.started_at).getTime()) / 1000);
  }
</script>

<div class="space-y-4">
  <div class="flex flex-wrap items-center gap-3">
    <div class="w-full max-w-sm sm:w-80">
      <Segmented
        bind:value={status}
        size="sm"
        ariaLabel="状态筛选"
        options={[
          { value: 'all', label: '全部' },
          { value: 'active', label: '进行中' },
          { value: 'succeeded', label: '成功' },
          { value: 'failed', label: '失败/取消' },
        ]}
      />
    </div>
    <div class="relative w-full sm:w-64">
      <Search class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
      <input class="field !h-9 pl-9" placeholder="文件名、用户名或任务 ID" bind:value={query} oninput={onSearch} />
    </div>
    <span class="text-[13px] text-muted">共 {total} 个</span>
    <button class="btn btn-ghost btn-sm btn-icon ml-auto" onclick={() => load()} aria-label="刷新">
      <RefreshCw class="size-4 {loading ? 'animate-spin' : ''}" />
    </button>
  </div>

  <div class="card overflow-x-auto">
    <table class="table min-w-[860px]">
      <thead>
        <tr>
          <th>文件</th>
          <th>用户</th>
          <th>状态</th>
          <th class="text-right">页数</th>
          <th>模型</th>
          <th>提交时间</th>
          <th>耗时</th>
          <th class="text-right">Tokens</th>
          <th class="text-right">操作</th>
        </tr>
      </thead>
      <tbody>
        {#each items as job (job.id)}
          <tr>
            <td class="max-w-64">
              <p class="truncate font-medium" title={job.filename}>{job.filename}</p>
              <p class="truncate text-[12px] text-muted">
                {job.id} · {bytes(job.file_size)} · {session.languageLabel(job.lang_in)}→{session.languageLabel(job.lang_out)}
              </p>
              {#if job.status === 'failed' && job.error}
                <p class="mt-0.5 line-clamp-2 text-[12px] text-bad-ink" title={job.error}>{job.error}</p>
              {:else if job.status === 'running'}
                <p class="mt-0.5 text-[12px] text-accent-ink">{jobStageLabel(job.stage)} · {Math.floor(job.progress)}%</p>
              {/if}
            </td>
            <td class="whitespace-nowrap">@{job.username}</td>
            <td><StatusBadge status={job.status} /></td>
            <td class="tabular text-right">{job.billed_pages}</td>
            <td class="max-w-36 truncate">{job.model_name}</td>
            <td class="whitespace-nowrap text-ink-2">{dateTime(job.created_at)}</td>
            <td class="whitespace-nowrap text-ink-2">{runtime(job)}</td>
            <td class="tabular text-right text-ink-2">{job.tokens ? compact(job.tokens) : '—'}</td>
            <td>
              <div class="flex justify-end gap-1">
                <button class="btn btn-ghost btn-sm btn-icon" title="查看引擎日志" aria-label="查看引擎日志" onclick={() => openLog(job)}>
                  <ScrollText class="size-4" />
                </button>
                {#if job.status === 'running' || job.status === 'queued'}
                  <button class="btn btn-ghost btn-sm btn-icon" title="取消" aria-label="取消" onclick={() => act(job, 'cancel')}>
                    <Ban class="size-4" />
                  </button>
                {:else if (job.status === 'failed' || job.status === 'canceled') && !job.files_purged}
                  <button class="btn btn-ghost btn-sm btn-icon" title="重试" aria-label="重试" onclick={() => act(job, 'retry')}>
                    <RotateCcw class="size-4" />
                  </button>
                {/if}
                <button class="btn btn-ghost btn-sm btn-icon text-bad-ink" title="删除" aria-label="删除" onclick={() => act(job, 'delete')}>
                  <Trash2 class="size-4" />
                </button>
              </div>
            </td>
          </tr>
        {:else}
          <tr><td colspan="9" class="py-12 text-center text-muted">{loading ? '加载中…' : '没有符合条件的任务'}</td></tr>
        {/each}
      </tbody>
    </table>
  </div>
  {#if items.length < total}
    <div class="flex justify-center">
      <button class="btn btn-secondary" disabled={loading} onclick={() => load(true)}>加载更多</button>
    </div>
  {/if}
</div>

<Modal open={!!logJob} title="引擎日志" description={logJob?.filename} size="lg" onclose={() => (logJob = null)}>
  {#if logLoading}
    <div class="flex justify-center py-10"><LoaderCircle class="size-5 animate-spin text-muted" /></div>
  {:else}
    <pre class="max-h-[60dvh] overflow-auto rounded-xl bg-surface-2 p-3 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap break-all text-ink-2">{logText}</pre>
  {/if}
</Modal>
