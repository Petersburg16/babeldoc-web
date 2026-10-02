<script lang="ts">
  interface Props {
    value: number;
    active?: boolean;
    tone?: 'accent' | 'good' | 'warn' | 'bad';
    height?: string;
    label?: string;
  }

  let { value, active = false, tone = 'accent', height = 'h-1.5', label }: Props = $props();

  const fills = { accent: 'bg-accent', good: 'bg-good', warn: 'bg-warn', bad: 'bg-bad' };
  const pct = $derived(Math.max(0, Math.min(100, value || 0)));
</script>

<div
  class="relative w-full overflow-hidden rounded-full bg-accent-track/55 {height}"
  role="progressbar"
  aria-label={label}
  aria-valuemin={0}
  aria-valuemax={100}
  aria-valuenow={Math.round(pct)}
>
  <div class="h-full rounded-full transition-[width] duration-500 ease-out {fills[tone]}" style="width: {pct}%"></div>
  {#if active}
    <div
      class="pointer-events-none absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-transparent via-white/40 to-transparent"
      style="animation: shimmer 1.6s ease-in-out infinite"
    ></div>
  {/if}
</div>
