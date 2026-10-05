<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { AudioLines, CircleAlert, Megaphone, RefreshCw } from '../../lib/icons';
  import { meetings } from '../../lib/meetings.svelte';
  import { session } from '../../lib/session.svelte';
  import { toast } from '../../lib/toast.svelte';
  import MeetingCard from './MeetingCard.svelte';
  import UploadCard from './UploadCard.svelte';

  let now = $state(Date.now());
  let ticker: ReturnType<typeof setInterval> | undefined;
  let optionsError = $state('');
  let listError = $state('');

  const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

  async function loadOptions() {
    optionsError = '';
    try {
      await meetings.loadOptions();
    } catch (e) {
      optionsError = message(e);
    }
  }

  async function load(more = false) {
    try {
      await meetings.load(more);
      listError = '';
    } catch (e) {
      if (meetings.loaded) toast.error(e);
      else listError = message(e);
    }
  }

  onMount(() => {
    ticker = setInterval(() => (now = Date.now()), 1000);
    void loadOptions();
    if (!meetings.loaded) void load();
  });

  onDestroy(() => clearInterval(ticker));
</script>

<div class="space-y-8">
  <section>
    <h1 class="text-[22px] font-semibold tracking-tight sm:text-2xl">会议记录</h1>
    <p class="mt-1 text-[13.5px] text-muted">上传会议录音，自动区分说话人、整理逐字稿并生成纪要。</p>
  </section>

  {#if session.meta?.announcement}
    <div class="flex gap-3 rounded-2xl border border-accent/25 bg-accent-soft px-4 py-3 text-[13.5px] leading-relaxed">
      <Megaphone class="mt-0.5 size-4 shrink-0 text-accent" />
      <p class="whitespace-pre-line text-ink">{session.meta.announcement}</p>
    </div>
  {/if}

  <div class="grid gap-x-6 gap-y-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:items-start">
    <UploadCard {optionsError} onretry={loadOptions} />

    <section class="min-w-0">
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <h2 class="text-[17px] font-semibold tracking-tight">我的会议</h2>
        {#if meetings.loaded}<span class="text-[13px] text-muted">{meetings.total} 场</span>{/if}
        <button
          class="btn btn-ghost btn-sm btn-icon ml-auto"
          onclick={() => load()}
          disabled={meetings.loading}
          aria-label="刷新"
          title="刷新"
        >
          <RefreshCw class="size-4 {meetings.loading ? 'animate-spin' : ''}" />
        </button>
      </div>

      {#if meetings.items.length}
        <div class="space-y-3">
          {#each meetings.items as meeting (meeting.id)}
            <MeetingCard {meeting} {now} />
          {/each}
        </div>
        {#if meetings.hasMore}
          <div class="mt-4 flex justify-center">
            <button class="btn btn-secondary" disabled={meetings.loading} onclick={() => load(true)}>加载更多</button>
          </div>
        {/if}
      {:else if meetings.loaded}
        <div class="card flex flex-col items-center px-6 py-14 text-center">
          <div class="grid size-14 place-items-center rounded-2xl bg-surface-2">
            <AudioLines class="size-6 text-muted" strokeWidth={1.5} />
          </div>
          <p class="mt-4 font-medium">还没有会议记录</p>
          <p class="mt-1 text-[13px] text-muted">上传第一段会议录音试试，识别和整理的进度会实时显示在这里。</p>
        </div>
      {:else if listError}
        <div class="card flex flex-col items-center px-6 py-14 text-center">
          <div class="grid size-14 place-items-center rounded-2xl bg-bad-soft">
            <CircleAlert class="size-6 text-bad-ink" strokeWidth={1.5} />
          </div>
          <p class="mt-4 font-medium">会议列表加载失败</p>
          <p class="mt-1 text-[13px] break-words text-muted">{listError}</p>
          <button class="btn btn-secondary mt-5" disabled={meetings.loading} onclick={() => load()}>重新加载</button>
        </div>
      {:else}
        <div class="space-y-3">
          {#each [0, 1, 2] as i (i)}
            <div class="card h-28 animate-pulse bg-surface-2/50"></div>
          {/each}
        </div>
      {/if}
    </section>
  </div>
</div>
