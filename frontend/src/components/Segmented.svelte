<script lang="ts" generics="T extends string">
  interface Props {
    value: T;
    options: { value: T; label: string; disabled?: boolean }[];
    size?: 'sm' | 'md';
    ariaLabel?: string;
  }

  let { value = $bindable(), options, size = 'md', ariaLabel }: Props = $props();
</script>

<div
  class="inline-flex w-full rounded-[10px] bg-surface-2 p-[3px] {size === 'sm' ? 'text-[12.5px]' : 'text-[13px]'}"
  role="radiogroup"
  aria-label={ariaLabel}
>
  {#each options as option (option.value)}
    <button
      type="button"
      role="radio"
      aria-checked={value === option.value}
      disabled={option.disabled}
      class="flex-1 rounded-[8px] px-2.5 font-medium whitespace-nowrap transition-all {size === 'sm' ? 'h-7' : 'h-8'} {value ===
      option.value
        ? 'bg-surface text-ink shadow-card'
        : 'text-ink-2 hover:text-ink'} disabled:opacity-40"
      onclick={() => (value = option.value)}
    >
      {option.label}
    </button>
  {/each}
</div>
