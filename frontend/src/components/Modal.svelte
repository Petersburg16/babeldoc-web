<script lang="ts">
  import type { Snippet } from 'svelte';
  import { X } from '../lib/icons';

  interface Props {
    open: boolean;
    title: string;
    description?: string;
    size?: 'sm' | 'md' | 'lg';
    onclose: () => void;
    children: Snippet;
    footer?: Snippet;
  }

  let { open, title, description, size = 'md', onclose, children, footer }: Props = $props();
  let dialog = $state<HTMLDialogElement>();

  $effect(() => {
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  });

  const widths = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl' };
</script>

<dialog
  bind:this={dialog}
  class="m-auto w-[calc(100%-2rem)] {widths[size]} rounded-2xl border border-line bg-surface p-0 text-ink shadow-pop backdrop:bg-black/40 backdrop:backdrop-blur-[2px]"
  oncancel={(e) => {
    e.preventDefault();
    onclose();
  }}
  onclick={(e) => {
    if (e.target === dialog) onclose();
  }}
>
  {#if open}
    <div class="animate-pop flex max-h-[85dvh] flex-col">
      <div class="flex items-start gap-3 px-5 pt-5 pb-3">
        <div class="min-w-0 flex-1">
          <h2 class="text-[15px] font-semibold">{title}</h2>
          {#if description}<p class="mt-0.5 text-[13px] text-muted">{description}</p>{/if}
        </div>
        <button class="btn btn-ghost btn-sm btn-icon -mt-1 -mr-2" onclick={onclose} aria-label="关闭">
          <X class="size-4" />
        </button>
      </div>
      <div class="overflow-y-auto px-5 pb-5">
        {@render children()}
      </div>
      {#if footer}
        <div class="flex justify-end gap-2 border-t border-line bg-surface-2/60 px-5 py-3">
          {@render footer()}
        </div>
      {/if}
    </div>
  {/if}
</dialog>
