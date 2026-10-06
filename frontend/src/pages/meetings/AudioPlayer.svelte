<script lang="ts">
  import Menu from '../../components/Menu.svelte';
  import { Check, RefreshCw } from '../../lib/icons';
  import { clock } from '../../lib/meeting/format';
  import { FastForward, Pause, Play, Rewind, VolumeX } from '../../lib/meeting/icons';
  import { toast } from '../../lib/toast.svelte';

  interface Props {
    src: string;
    /** false：录音已过保留期删除，只显示提示 */
    available: boolean;
    /** 录音元数据加载出来之前，先用会议记录里的时长 */
    durationMs: number;
    currentMs?: number;
    playing?: boolean;
  }

  let { src, available, durationMs, currentMs = $bindable(0), playing = $bindable(false) }: Props = $props();

  const RATES = [1, 1.25, 1.5, 2];
  const RATE_KEY = 'bdw-meeting-rate';
  const STEP = 15_000;

  let audio = $state<HTMLAudioElement>();
  let track = $state<HTMLElement>();
  let mediaMs = $state(0);
  let rate = $state(readRate());
  let failed = $state(false);
  let dragMs = $state<number | null>(null);
  let hoverMs = $state<number | null>(null);
  // 元数据还没加载时设置 currentTime 不可靠，先记下来，loadedmetadata 时再跳
  let pendingSeek: number | null = null;

  const totalMs = $derived(mediaMs > 0 ? mediaMs : durationMs);
  const shownMs = $derived(dragMs ?? currentMs);
  const pct = $derived(ratio(shownMs));
  const tipMs = $derived(dragMs ?? hoverMs);

  function ratio(ms: number) {
    return totalMs > 0 ? Math.min(100, Math.max(0, (ms / totalMs) * 100)) : 0;
  }

  function readRate() {
    try {
      const saved = Number(localStorage.getItem(RATE_KEY));
      return RATES.includes(saved) ? saved : 1;
    } catch {
      return 1;
    }
  }

  function setRate(value: number) {
    rate = value;
    try {
      localStorage.setItem(RATE_KEY, String(value));
    } catch {
      /* 忽略 */
    }
  }

  $effect(() => {
    if (!audio) return;
    // 重新加载音频时 playbackRate 会被重置成 defaultPlaybackRate，两个都设
    audio.defaultPlaybackRate = rate;
    audio.playbackRate = rate;
  });

  async function play() {
    if (!audio) return;
    try {
      await audio.play();
    } catch (e) {
      // 紧接着又暂停或换了位置时会抛 AbortError，不算出错
      if (e instanceof DOMException && (e.name === 'AbortError' || e.name === 'NotAllowedError')) return;
      failed = true;
    }
  }

  function toggle() {
    if (!audio || failed) return;
    if (audio.paused) void play();
    else audio.pause();
  }

  /** 跳到会议内的某个时刻（毫秒）；autoplay 为 true 时顺带开始播放 */
  export function seek(ms: number, autoplay = true) {
    if (!available) {
      toast.info('录音已过保留期删除，只能查看文字');
      return;
    }
    if (!audio) return;
    const target = Math.max(0, totalMs > 0 ? Math.min(ms, totalMs) : ms);
    currentMs = target;
    if (audio.readyState === HTMLMediaElement.HAVE_NOTHING) pendingSeek = target;
    else audio.currentTime = target / 1000;
    if (autoplay && audio.paused) void play();
  }

  function skip(delta: number) {
    seek((audio ? audio.currentTime * 1000 : currentMs) + delta, false);
  }

  function retry() {
    failed = false;
    audio?.load();
  }

  function syncDuration() {
    if (audio && Number.isFinite(audio.duration) && audio.duration > 0) mediaMs = audio.duration * 1000;
  }

  function onloadedmetadata() {
    syncDuration();
    if (audio && pendingSeek !== null) {
      audio.currentTime = pendingSeek / 1000;
      pendingSeek = null;
    }
  }

  function syncTime() {
    if (audio) currentMs = audio.currentTime * 1000;
  }

  function msAt(event: PointerEvent) {
    if (!track) return 0;
    const rect = track.getBoundingClientRect();
    const r = rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0;
    return Math.min(1, Math.max(0, r)) * totalMs;
  }

  function onpointerdown(event: PointerEvent) {
    if (!totalMs || event.button !== 0 || failed) return;
    track?.setPointerCapture(event.pointerId);
    dragMs = msAt(event);
  }

  function onpointermove(event: PointerEvent) {
    if (dragMs !== null) dragMs = msAt(event);
    else if (event.pointerType === 'mouse' && totalMs) hoverMs = msAt(event);
  }

  // 拖动时只更新显示，松手才真正跳转，免得拖一下发出几十个范围请求
  function onpointerup() {
    if (dragMs === null) return;
    const target = dragMs;
    dragMs = null;
    seek(target, false);
  }

  function onsliderkey(event: KeyboardEvent) {
    const base = audio ? audio.currentTime * 1000 : currentMs;
    const moves: Record<string, number> = {
      ArrowLeft: base - 5000,
      ArrowDown: base - 5000,
      ArrowRight: base + 5000,
      ArrowUp: base + 5000,
      PageDown: base - 60_000,
      PageUp: base + 60_000,
      Home: 0,
      End: totalMs,
    };
    if (!(event.key in moves)) return;
    event.preventDefault();
    seek(moves[event.key], false);
  }

  // 空格播放暂停：在输入框、按钮、下拉框里或有弹窗时不接管
  function onwindowkey(event: KeyboardEvent) {
    if (event.key !== ' ' || event.repeat || event.isComposing || event.defaultPrevented) return;
    if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    if (!available || failed || !audio) return;
    const target = event.target as HTMLElement | null;
    if (target?.isContentEditable || target?.closest('input, textarea, select, button, [role="radio"], [role="menu"]')) return;
    if (document.querySelector('dialog[open]')) return;
    event.preventDefault();
    toggle();
  }
</script>

<svelte:window onkeydown={onwindowkey} />

{#if !available}
  <div class="flex items-center gap-2.5 rounded-xl border border-line bg-surface-2 px-4 py-3 text-[13px] text-muted">
    <VolumeX class="size-4 shrink-0" />
    录音已过保留期删除，仅保留文字
  </div>
{:else}
  <div class="card flex flex-wrap items-center gap-x-2 gap-y-1 px-2 py-1.5 sm:flex-nowrap sm:gap-x-3 sm:px-3 sm:py-2">
    <audio
      bind:this={audio}
      {src}
      preload="metadata"
      {onloadedmetadata}
      ondurationchange={syncDuration}
      ontimeupdate={syncTime}
      onseeked={syncTime}
      onplay={() => (playing = true)}
      onpause={() => (playing = false)}
      onended={() => (playing = false)}
      onerror={() => {
        failed = true;
        playing = false;
      }}
    ></audio>

    <div class="flex items-center gap-0.5">
      <button class="btn btn-ghost btn-icon" disabled={failed} onclick={() => skip(-STEP)} title="后退 15 秒" aria-label="后退 15 秒">
        <Rewind class="size-[18px]" />
      </button>
      <button
        class="grid size-10 shrink-0 place-items-center rounded-full bg-accent text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
        disabled={failed}
        onclick={toggle}
        title="{playing ? '暂停' : '播放'}（空格键）"
        aria-label={playing ? '暂停' : '播放'}
      >
        {#if playing}
          <Pause class="size-[18px]" fill="currentColor" />
        {:else}
          <Play class="size-[18px] translate-x-px" fill="currentColor" />
        {/if}
      </button>
      <button class="btn btn-ghost btn-icon" disabled={failed} onclick={() => skip(STEP)} title="前进 15 秒" aria-label="前进 15 秒">
        <FastForward class="size-[18px]" />
      </button>
    </div>

    <div
      bind:this={track}
      class="relative order-last h-7 basis-full touch-none select-none sm:order-none sm:basis-0 sm:flex-1 {failed
        ? 'opacity-50'
        : 'cursor-pointer'}"
      role="slider"
      tabindex="0"
      aria-label="播放进度"
      aria-valuemin={0}
      aria-valuemax={Math.round(totalMs / 1000)}
      aria-valuenow={Math.round(shownMs / 1000)}
      aria-valuetext={clock(shownMs)}
      {onpointerdown}
      {onpointermove}
      {onpointerup}
      onpointercancel={() => (dragMs = null)}
      onpointerleave={() => (hoverMs = null)}
      onkeydown={onsliderkey}
    >
      <div class="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 overflow-hidden rounded-full bg-accent-track/55">
        <div class="h-full rounded-full bg-accent" style="width: {pct}%"></div>
      </div>
      <div
        class="pointer-events-none absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-accent shadow-card transition-transform {dragMs !==
        null
          ? 'scale-125'
          : ''}"
        style="left: {pct}%"
      ></div>
      {#if tipMs !== null}
        <div
          class="tabular pointer-events-none absolute bottom-full mb-0.5 -translate-x-1/2 rounded-md bg-ink px-1.5 py-0.5 text-[11px] whitespace-nowrap text-page"
          style="left: {ratio(tipMs)}%"
        >
          {clock(tipMs)}
        </div>
      {/if}
    </div>

    {#if failed}
      <span class="flex items-center gap-1 text-[12.5px] text-bad-ink">
        录音加载失败
        <button class="btn btn-ghost btn-sm" onclick={retry}><RefreshCw class="size-3.5" /> 重试</button>
      </span>
    {:else}
      <span class="tabular px-1 text-[12.5px] whitespace-nowrap text-ink-2">
        {clock(shownMs)}<span class="text-muted"> / {clock(totalMs)}</span>
      </span>
    {/if}

    <div class="ml-auto sm:ml-0">
      <Menu width="w-32">
        {#snippet trigger({ toggle: toggleMenu, open })}
          <button
            class="btn btn-ghost btn-sm tabular min-w-12"
            aria-expanded={open}
            onclick={toggleMenu}
            title="播放速度"
            aria-label="播放速度 {rate} 倍"
          >
            {rate}×
          </button>
        {/snippet}
        {#snippet children({ close })}
          {#each RATES as value (value)}
            <button
              class="menu-item"
              onclick={() => {
                setRate(value);
                close();
              }}
            >
              <span class="tabular flex-1">{value}×{value === 1 ? '（正常）' : ''}</span>
              {#if value === rate}<Check class="size-4 text-accent" />{/if}
            </button>
          {/each}
        {/snippet}
      </Menu>
    </div>
  </div>
{/if}
