<script lang="ts">
  import type { Component } from 'svelte';
  import Logo from './Logo.svelte';

  interface Props {
    /** 页面模块的动态 import，例如 () => import('./pdf/PdfApp.svelte')；打包时会切成单独的文件 */
    load: () => Promise<{ default: Component }>;
    /** 加载失败时显示的名称，例如“PDF 工具” */
    label: string;
  }

  let { load, label }: Props = $props();

  // 挂载时下载一次；在同一功能里切换页面不会重新加载
  const app = $derived(load());
</script>

{#await app}
  <div class="grid place-items-center py-24"><Logo class="size-9 animate-pulse" /></div>
{:then module}
  <module.default />
{:catch error}
  <div class="grid place-items-center py-24 text-center">
    <div>
      <p class="font-medium">{label}加载失败</p>
      <p class="mt-1 text-[13px] text-muted">{error instanceof Error ? error.message : String(error)}</p>
      <button class="btn btn-secondary mt-5" onclick={() => location.reload()}>刷新重试</button>
    </div>
  </div>
{/await}
