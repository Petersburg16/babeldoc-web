<script lang="ts">
  import type { Component } from 'svelte';
  import { CircleAlert, TriangleAlert } from '../../lib/icons';

  interface Props {
    label: string;
    ratio: number;
    detail: string;
    icon: Component<{ class?: string }>;
  }

  let { label, ratio, detail, icon: Icon }: Props = $props();
  const pct = $derived(Math.max(0, Math.min(100, ratio * 100)));
  const level = $derived(pct >= 90 ? 'bad' : pct >= 80 ? 'warn' : 'ok');
</script>

<div>
  <div class="flex items-center gap-2 text-[13px]">
    <Icon class="size-4 text-muted" />
    <span class="text-ink-2">{label}</span>
    {#if level === 'bad'}
      <span class="flex items-center gap-1 text-[12px] font-medium text-bad-ink"><CircleAlert class="size-3.5" />紧张</span>
    {:else if level === 'warn'}
      <span class="flex items-center gap-1 text-[12px] font-medium text-warn-ink"><TriangleAlert class="size-3.5" />偏高</span>
    {/if}
    <span class="tabular ml-auto font-medium">{Math.round(pct)}%</span>
  </div>
  <div
    class="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-accent-track/60"
    role="meter"
    aria-label={label}
    aria-valuemin={0}
    aria-valuemax={100}
    aria-valuenow={Math.round(pct)}
  >
    <div
      class="h-full rounded-full transition-[width] duration-700 {level === 'bad' ? 'bg-bad' : level === 'warn' ? 'bg-warn' : 'bg-accent'}"
      style="width: {pct}%"
    ></div>
  </div>
  <p class="mt-1 text-[12px] text-muted">{detail}</p>
</div>
