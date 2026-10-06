<script lang="ts">
  import type { Component } from 'svelte';
  import { Plus } from '../../lib/icons';

  /** 后台配置页“还没有配置”的空状态卡片；action 是下面的“添加”按钮，不传就不显示 */
  interface Props {
    icon: Component<{ class?: string }>;
    title: string;
    text: string;
    action?: { label: string; onclick: () => void };
  }

  let { icon: Icon, title, text, action }: Props = $props();
</script>

<div class="card flex flex-col items-center px-6 py-14 text-center">
  <div class="grid size-14 place-items-center rounded-2xl bg-accent-soft text-accent"><Icon class="size-6" /></div>
  <p class="mt-4 font-medium">{title}</p>
  <p class="mt-1 max-w-sm text-[13px] text-muted">{text}</p>
  {#if action}
    <button class="btn btn-primary mt-5" onclick={action.onclick}><Plus class="size-4" />{action.label}</button>
  {/if}
</div>
