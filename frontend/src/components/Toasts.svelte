<script lang="ts">
  import { CircleAlert, CircleCheck, Info, X } from '../lib/icons';
  import { toast } from '../lib/toast.svelte';

  const icons = { success: CircleCheck, error: CircleAlert, info: Info };
  const tones = { success: 'text-good', error: 'text-bad', info: 'text-accent' };
</script>

<div class="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4 sm:items-end sm:pr-6" aria-live="polite">
  {#each toast.items as item (item.id)}
    {@const Icon = icons[item.kind]}
    <div class="animate-pop pointer-events-auto flex w-full max-w-sm items-start gap-2.5 rounded-xl border border-line bg-surface px-3.5 py-3 shadow-pop">
      <Icon class="mt-0.5 size-4 shrink-0 {tones[item.kind]}" />
      <p class="min-w-0 flex-1 text-[13px] leading-relaxed break-words">{item.message}</p>
      <button class="-m-1 rounded-md p-1 text-muted hover:bg-surface-2 hover:text-ink" onclick={() => toast.dismiss(item.id)} aria-label="关闭">
        <X class="size-3.5" />
      </button>
    </div>
  {/each}
</div>
