<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    trigger: Snippet<[{ toggle: () => void; open: boolean }]>;
    children: Snippet<[{ close: () => void }]>;
    width?: string;
  }

  let { trigger, children, width = 'w-48' }: Props = $props();
  let open = $state(false);
  let root = $state<HTMLElement>();

  function onWindowClick(event: MouseEvent) {
    if (open && root && !root.contains(event.target as Node)) open = false;
  }
</script>

<svelte:window onclick={onWindowClick} onkeydown={(e) => e.key === 'Escape' && (open = false)} />

<div class="relative" bind:this={root}>
  {@render trigger({ toggle: () => (open = !open), open })}
  {#if open}
    <div
      class="animate-pop absolute top-full right-0 z-40 mt-1.5 {width} rounded-xl border border-line bg-surface p-1 shadow-pop"
      role="menu"
    >
      {@render children({ close: () => (open = false) })}
    </div>
  {/if}
</div>
