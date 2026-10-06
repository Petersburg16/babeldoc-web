<script lang="ts">
  import type { Snippet } from 'svelte';

  // 工具选项里的页码范围输入框（本站页码写法，见 lib/pdf/ranges.ts）。校验由工具页自己算（按钮是否可用也看它），这里只负责显示
  interface Props {
    id: string;
    label: string;
    /** 标签后注明“（可选）” */
    optional?: boolean;
    placeholder: string;
    value: string;
    /** 写法有误时的提示，有错时代替 hint 显示 */
    error?: string;
    hint?: string | Snippet;
  }

  let { id, label, optional = false, placeholder, value = $bindable(), error = '', hint }: Props = $props();
</script>

<div>
  <label class="label" for={id}>
    {label}{#if optional}{' '}<span class="font-normal text-muted">（可选）</span>{/if}
  </label>
  <input
    {id}
    class="field font-mono text-[13px] placeholder:font-sans"
    {placeholder}
    autocomplete="off"
    bind:value
    aria-invalid={!!error}
    aria-describedby={error || hint ? `${id}-note` : undefined}
  />
  {#if error}
    <p id="{id}-note" class="mt-1 text-[12px] text-bad-ink">{error}</p>
  {:else if typeof hint === 'string'}
    <p id="{id}-note" class="hint">{hint}</p>
  {:else if hint}
    <p id="{id}-note" class="hint">{@render hint()}</p>
  {/if}
</div>
