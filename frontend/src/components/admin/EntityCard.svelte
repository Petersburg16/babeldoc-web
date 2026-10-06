<script lang="ts">
  import type { Component, Snippet } from 'svelte';
  import { Star } from '../../lib/icons';

  /** 后台配置页的一张卡片（翻译模型、会议模型、整理方案、识别服务）：只管外观，数据和操作由页面传入 */
  interface Props {
    icon: Component<{ class?: string }>;
    name: string;
    description?: string;
    enabled: boolean;
    /** 不传表示这类配置没有“默认”一说 */
    isDefault?: boolean;
    /** 条目里是一排小标签时把行距放宽一点 */
    spacious?: boolean;
    /** dl 里的 dt/dd */
    details: Snippet;
    /** 列表和操作栏之间：测试结果、问题提示等 */
    notes?: Snippet;
    actions: Snippet;
  }

  let { icon: Icon, name, description, enabled, isDefault = false, spacious = false, details, notes, actions }: Props = $props();
</script>

<div class="card flex flex-col p-5 {enabled ? '' : 'opacity-65'}">
  <div class="flex items-start gap-3">
    <div class="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent"><Icon class="size-5" /></div>
    <div class="min-w-0 flex-1">
      <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h3 class="truncate text-[15px] font-semibold">{name}</h3>
        {#if isDefault}
          <span class="inline-flex items-center gap-1 rounded-full bg-warn-soft px-2 py-0.5 text-[11.5px] font-medium text-warn-ink">
            <Star class="size-3" />默认
          </span>
        {/if}
        {#if !enabled}<span class="rounded-full bg-surface-3 px-2 py-0.5 text-[11.5px] text-muted">已停用</span>{/if}
      </div>
      {#if description}<p class="mt-0.5 text-[12.5px] text-muted">{description}</p>{/if}
    </div>
  </div>

  <dl class="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 {spacious ? 'gap-y-2' : 'gap-y-1.5'} text-[12.5px]">
    {@render details()}
  </dl>

  {@render notes?.()}

  <div class="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3">
    {@render actions()}
  </div>
</div>
