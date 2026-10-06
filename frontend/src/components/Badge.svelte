<script lang="ts">
  import type { Component } from 'svelte';

  interface Props {
    icon: Component;
    label: string;
    /** 底色和文字颜色，例如 "bg-good-soft text-good-ink" */
    tone: string;
    /** 图标转圈，表示正在进行 */
    spin?: boolean;
  }

  let { icon: Icon, label, tone, spin = false }: Props = $props();
</script>

<!-- 状态徽章的外观：翻译任务（StatusBadge）和会议（MeetingStatusBadge）各自维护状态表，共用这个样式 -->
<span class="inline-flex h-6 items-center gap-1 rounded-full pr-2.5 pl-2 text-[12px] font-medium whitespace-nowrap {tone}">
  <Icon class="size-3.5 {spin ? 'animate-spin' : ''}" />
  {label}
</span>
