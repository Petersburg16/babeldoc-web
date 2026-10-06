<script lang="ts">
  import type { Snippet } from 'svelte';
  import { CircleCheck, CircleX } from '../../lib/icons';

  /** 测试、检查结果的绿 / 红提示框；children 显示在说明下面（比如实际发送的参数） */
  interface Props {
    ok: boolean;
    text: string;
    children?: Snippet;
  }

  let { ok, text, children }: Props = $props();
</script>

<div class="mt-3 rounded-xl px-3 py-2 text-[12.5px] {ok ? 'bg-good-soft text-good-ink' : 'bg-bad-soft text-bad-ink'}">
  <div class="flex gap-2">
    {#if ok}<CircleCheck class="mt-0.5 size-4 shrink-0" />{:else}<CircleX class="mt-0.5 size-4 shrink-0" />{/if}
    <p class="min-w-0 break-words">{text}</p>
  </div>
  {@render children?.()}
</div>
