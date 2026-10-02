<script lang="ts">
  import { compact, number } from '../../lib/format';
  import { ChartColumn, ScrollText } from '../../lib/icons';
  import type { DailyPoint } from '../../lib/types';

  let { data }: { data: DailyPoint[] } = $props();

  const PLOT_H = 206;
  const AXIS_H = 26;
  const PAD_L = 36;
  const PAD_R = 8;
  const PAD_T = 18;
  const WEEK = ['日', '一', '二', '三', '四', '五', '六'];

  let width = $state(600);
  let hovered = $state<number | null>(null);
  let showTable = $state(false);

  function niceStep(raw: number) {
    if (raw <= 1) return 1;
    const power = 10 ** Math.floor(Math.log10(raw));
    const n = raw / power;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * power;
  }

  const max = $derived(Math.max(0, ...data.map((d) => d.pages)));
  const step = $derived(niceStep(Math.max(max, 4) / 4));
  const top = $derived(step * Math.max(1, Math.ceil(max / step)));
  const ticks = $derived(Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step));
  const plotW = $derived(Math.max(100, width - PAD_L - PAD_R));
  const band = $derived(plotW / Math.max(1, data.length));
  const barW = $derived(Math.min(24, Math.max(6, band * 0.62)));
  const y = (v: number) => PAD_T + PLOT_H - (v / top) * PLOT_H;
  const maxIndex = $derived(max > 0 ? data.findIndex((d) => d.pages === max) : -1);
  const labelEvery = $derived(band < 34 ? 2 : 1);

  function dayLabel(day: string) {
    const [, m, d] = day.split('-').map(Number);
    return `${m}/${d}`;
  }

  function weekday(day: string) {
    return `周${WEEK[new Date(`${day}T00:00:00`).getDay()]}`;
  }

  function barPath(x: number, value: number) {
    const h = Math.max(0, y(0) - y(value));
    if (h <= 0) return '';
    const r = Math.min(4, h, barW / 2);
    const bottom = y(0);
    const topY = bottom - h;
    return `M${x},${bottom}V${topY + r}Q${x},${topY} ${x + r},${topY}H${x + barW - r}Q${x + barW},${topY} ${x + barW},${topY + r}V${bottom}Z`;
  }

  const tip = $derived(hovered !== null ? data[hovered] : null);
  const tipLeft = $derived(hovered !== null ? PAD_L + band * hovered + band / 2 : 0);
</script>

<figure class="card p-5">
  <figcaption class="flex items-center gap-2">
    <ChartColumn class="size-4 text-accent" />
    <span class="text-[14.5px] font-semibold">近 14 天翻译页数</span>
    <span class="text-[12.5px] text-muted">失败、取消的任务不计</span>
    <button class="btn btn-ghost btn-sm ml-auto" onclick={() => (showTable = !showTable)} aria-pressed={showTable}>
      {#if showTable}<ChartColumn class="size-3.5" />图表{:else}<ScrollText class="size-3.5" />表格{/if}
    </button>
  </figcaption>

  {#if showTable}
    <div class="mt-3 max-h-[230px] overflow-y-auto">
      <table class="table">
        <thead><tr><th>日期</th><th class="text-right">页数</th><th class="text-right">任务</th><th class="text-right">成功</th><th class="text-right">失败</th><th class="text-right">Tokens</th></tr></thead>
        <tbody>
          {#each data.slice().reverse() as d (d.day)}
            <tr>
              <td>{dayLabel(d.day)} <span class="text-muted">{weekday(d.day)}</span></td>
              <td class="tabular text-right">{number(d.pages)}</td>
              <td class="tabular text-right">{d.jobs}</td>
              <td class="tabular text-right">{d.succeeded}</td>
              <td class="tabular text-right">{d.failed}</td>
              <td class="tabular text-right">{compact(d.tokens)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else}
    <div class="relative mt-3" bind:clientWidth={width}>
      <svg width={width} height={PAD_T + PLOT_H + AXIS_H} role="img" aria-label="近 14 天每日翻译页数柱状图">
        {#each ticks as t (t)}
          <line x1={PAD_L} x2={width - PAD_R} y1={y(t)} y2={y(t)} stroke="var(--line)" stroke-width="1" shape-rendering="crispEdges" />
          <text x={PAD_L - 8} y={y(t)} dy="0.32em" text-anchor="end" class="tabular fill-muted text-[11px]">{compact(t)}</text>
        {/each}
        <line x1={PAD_L} x2={width - PAD_R} y1={y(0)} y2={y(0)} stroke="var(--line-strong)" stroke-width="1" shape-rendering="crispEdges" />
        {#each data as d, i (d.day)}
          {@const x = PAD_L + band * i + (band - barW) / 2}
          <path d={barPath(x, d.pages)} fill="var(--accent)" opacity={hovered === null || hovered === i ? 1 : 0.45} />
          {#if i === maxIndex}
            <text x={x + barW / 2} y={y(d.pages) - 6} text-anchor="middle" class="tabular fill-ink-2 text-[11px] font-medium">{number(d.pages)}</text>
          {/if}
          {#if i % labelEvery === (data.length - 1) % labelEvery}
            <text x={PAD_L + band * i + band / 2} y={PAD_T + PLOT_H + 17} text-anchor="middle" class="fill-muted text-[11px]">{dayLabel(d.day)}</text>
          {/if}
          <rect
            x={PAD_L + band * i}
            y={PAD_T}
            width={band}
            height={PLOT_H}
            fill="transparent"
            role="button"
            tabindex="0"
            aria-label="{dayLabel(d.day)} {d.pages} 页"
            onpointerenter={() => (hovered = i)}
            onpointerleave={() => (hovered = null)}
            onfocus={() => (hovered = i)}
            onblur={() => (hovered = null)}
            class="outline-none"
          />
        {/each}
      </svg>
      {#if tip}
        <div
          class="pointer-events-none absolute top-0 z-10 w-40 -translate-x-1/2 rounded-xl border border-line bg-surface px-3 py-2.5 text-[12px] shadow-pop"
          style="left: {Math.min(Math.max(tipLeft, 80), width - 80)}px"
        >
          <p class="text-muted">{dayLabel(tip.day)} {weekday(tip.day)}</p>
          <p class="mt-1 flex items-center gap-2">
            <span class="h-0.5 w-3 rounded-full bg-accent"></span>
            <span class="tabular text-[15px] font-semibold text-ink">{number(tip.pages)}</span>
            <span class="text-ink-2">页</span>
          </p>
          <div class="mt-1.5 space-y-0.5 border-t border-line pt-1.5 text-ink-2">
            <p class="flex justify-between"><span>任务</span><span class="tabular">{tip.jobs}</span></p>
            <p class="flex justify-between"><span>成功 / 失败</span><span class="tabular">{tip.succeeded} / {tip.failed}</span></p>
            <p class="flex justify-between"><span>Tokens</span><span class="tabular">{compact(tip.tokens)}</span></p>
          </div>
        </div>
      {/if}
    </div>
  {/if}
</figure>
