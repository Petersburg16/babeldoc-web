<script lang="ts">
  interface Props {
    checked: boolean;
    label: string;
    description?: string;
    disabled?: boolean;
    badge?: string;
  }

  let { checked = $bindable(), label, description, disabled = false, badge }: Props = $props();
</script>

<label class="flex items-start justify-between gap-4 py-2 {disabled ? 'opacity-50' : 'cursor-pointer'}">
  <span class="min-w-0">
    <span class="flex items-center gap-1.5 text-[13.5px] text-ink">
      {label}
      {#if badge}<span class="rounded bg-warn-soft px-1.5 py-px text-[11px] font-medium text-warn-ink">{badge}</span>{/if}
    </span>
    {#if description}<span class="mt-0.5 block text-[12px] leading-snug text-muted">{description}</span>{/if}
  </span>
  <span class="relative mt-0.5 inline-flex shrink-0">
    <input type="checkbox" class="peer sr-only" bind:checked {disabled} />
    <span class="h-5 w-9 rounded-full bg-line-strong transition-colors peer-checked:bg-accent peer-focus-visible:ring-2 peer-focus-visible:ring-accent peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-surface"></span>
    <span class="absolute top-0.5 left-0.5 size-4 rounded-full bg-white shadow-sm transition-transform peer-checked:translate-x-4"></span>
  </span>
</label>
