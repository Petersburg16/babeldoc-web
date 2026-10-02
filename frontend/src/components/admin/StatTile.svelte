<script lang="ts">
  import type { Component } from 'svelte';

  interface Props {
    label: string;
    value: string | number;
    sub?: string;
    icon: Component<{ class?: string }>;
  }

  let { label, value, sub, icon: Icon }: Props = $props();
</script>

<div class="card p-4">
  <div class="flex items-center gap-1.5 text-[12.5px] text-muted">
    <Icon class="size-3.5" />
    {label}
  </div>
  <p class="mt-2 text-[26px] leading-none font-semibold tracking-tight">{value}</p>
  {#if sub}<p class="mt-1.5 truncate text-[12px] text-muted">{sub}</p>{/if}
</div>
