<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import DailyChart from '../../components/admin/DailyChart.svelte';
  import Meter from '../../components/admin/Meter.svelte';
  import StatTile from '../../components/admin/StatTile.svelte';
  import ProgressBar from '../../components/ProgressBar.svelte';
  import { api } from '../../lib/api';
  import { bytes, compact, duration, elapsedSince, jobStageLabel, relativeTime } from '../../lib/format';
  import {
    CircleAlert,
    Clock,
    Cpu,
    FileText,
    HardDrive,
    LoaderCircle,
    MemoryStick,
    Server,
    Sparkles,
    Users,
    Languages,
    Gauge,
  } from '../../lib/icons';
  import { toast } from '../../lib/toast.svelte';
  import type { Stats } from '../../lib/types';

  let stats = $state<Stats | null>(null);
  let now = $state(Date.now());
  let timer: ReturnType<typeof setInterval> | undefined;
  let failedOnce = false;

  async function refresh() {
    try {
      stats = await api.admin.stats();
      now = Date.now();
      failedOnce = false;
    } catch (e) {
      if (!failedOnce) toast.error(e);
      failedOnce = true;
    }
  }

  onMount(() => {
    void refresh();
    timer = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh();
    }, 5000);
  });

  onDestroy(() => clearInterval(timer));
</script>

{#if !stats}
  <div class="flex justify-center py-20"><LoaderCircle class="size-6 animate-spin text-muted" /></div>
{:else}
  {@const s = stats}
  <div class="space-y-5">
    <div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <StatTile icon={LoaderCircle} label="正在翻译" value={s.jobs.by_status.running ?? 0} sub="并发上限 {s.engine.max_concurrent}" />
      <StatTile icon={Clock} label="排队中" value={s.jobs.by_status.queued ?? 0} />
      <StatTile icon={FileText} label="今日任务" value={s.jobs.today} />
      <StatTile icon={Languages} label="本月翻译页数" value={compact(s.jobs.month_pages)} sub="{s.jobs.month} 个任务" />
      <StatTile icon={Sparkles} label="本月 Tokens" value={compact(s.jobs.month_tokens)} />
      <StatTile icon={Users} label="可用用户" value={s.users.active} sub={s.users.total > s.users.active ? `另有 ${s.users.total - s.users.active} 个已停用` : "全部启用"} />
    </div>

    <div class="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <DailyChart data={s.daily} />

      <section class="card p-5">
        <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><Server class="size-4 text-accent" />服务器</h2>
        <div class="mt-4 space-y-4">
          <Meter
            icon={Cpu}
            label="CPU"
            ratio={s.system.cpu_percent / 100}
            detail="{s.system.cpu_count} 核{s.system.load ? ` · 负载 ${s.system.load.map((l) => l.toFixed(2)).join(' / ')}` : ''}"
          />
          <Meter
            icon={MemoryStick}
            label="内存"
            ratio={s.system.memory_used / s.system.memory_total}
            detail="已用 {bytes(s.system.memory_used)} / {bytes(s.system.memory_total)}"
          />
          <Meter
            icon={HardDrive}
            label="磁盘"
            ratio={(s.system.disk_total - s.system.disk_free) / s.system.disk_total}
            detail="剩余 {bytes(s.system.disk_free)} · 任务文件 {bytes(s.system.data_size)}"
          />
        </div>
        <dl class="mt-5 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-line pt-4 text-[12.5px]">
          <dt class="text-muted">翻译引擎</dt>
          <dd class="text-right font-medium">{s.engine.mode === 'mock' ? '模拟引擎' : `BabelDOC ${s.engine.version}`}</dd>
          <dt class="text-muted">在线页面</dt>
          <dd class="text-right font-medium">{s.engine.subscribers}</dd>
        </dl>
      </section>
    </div>

    <div class="grid gap-5 lg:grid-cols-2">
      <section class="card p-5">
        <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><Gauge class="size-4 text-accent" />处理队列</h2>
        {#if !s.running.length && !s.queued.length}
          <p class="mt-6 mb-4 text-center text-[13px] text-muted">队列空闲</p>
        {:else}
          <ul class="mt-3 space-y-3">
            {#each s.running as r (r.id)}
              <li>
                <div class="flex items-center gap-2 text-[13px]">
                  <span class="min-w-0 flex-1 truncate font-medium" title={r.filename}>{r.filename}</span>
                  <span class="shrink-0 text-muted">@{r.username}</span>
                </div>
                <div class="mt-1.5"><ProgressBar value={r.progress} active label="{r.filename} 进度" /></div>
                <p class="mt-1 flex justify-between text-[12px] text-muted">
                  <span>{jobStageLabel(r.stage)} · {r.billed_pages} 页</span>
                  <span class="tabular">{Math.floor(r.progress)}% · {duration(elapsedSince(r.started_at, now))}</span>
                </p>
              </li>
            {/each}
            {#each s.queued as q, i (q.id)}
              <li class="flex items-center gap-2 text-[13px]">
                <span class="tabular grid size-5 shrink-0 place-items-center rounded-full bg-surface-2 text-[11px] text-muted">{i + 1}</span>
                <span class="min-w-0 flex-1 truncate" title={q.filename}>{q.filename}</span>
                <span class="shrink-0 text-[12px] text-muted">@{q.username} · {q.billed_pages} 页</span>
              </li>
            {/each}
          </ul>
        {/if}
      </section>

      <section class="card p-5">
        <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><Users class="size-4 text-accent" />本月用量排行</h2>
        {#if s.top_users.length}
          <table class="table mt-2">
            <thead><tr><th>用户</th><th class="text-right">页数</th><th class="text-right">任务</th></tr></thead>
            <tbody>
              {#each s.top_users as u (u.username)}
                <tr>
                  <td>{u.display_name} <span class="text-muted">@{u.username}</span></td>
                  <td class="tabular text-right">{u.pages}</td>
                  <td class="tabular text-right">{u.jobs}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {:else}
          <p class="mt-6 mb-4 text-center text-[13px] text-muted">本月还没有任务</p>
        {/if}
      </section>
    </div>

    {#if s.failures.length}
      <section class="card p-5">
        <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><CircleAlert class="size-4 text-bad" />最近失败</h2>
        <ul class="mt-3 divide-y divide-line">
          {#each s.failures as f (f.id)}
            <li class="py-2.5 text-[13px]">
              <div class="flex items-center gap-2">
                <span class="min-w-0 flex-1 truncate font-medium">{f.filename}</span>
                <span class="shrink-0 text-[12px] text-muted">@{f.username} · {relativeTime(f.finished_at)}</span>
              </div>
              <p class="mt-0.5 line-clamp-2 text-[12.5px] text-bad-ink">{f.error}</p>
            </li>
          {/each}
        </ul>
        <a href="/admin/jobs" class="mt-2 inline-block text-[12.5px] text-accent hover:underline">去任务列表查看日志 →</a>
      </section>
    {/if}
  </div>
{/if}
