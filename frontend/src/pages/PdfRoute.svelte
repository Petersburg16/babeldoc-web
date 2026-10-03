<script lang="ts">
  import Logo from '../components/Logo.svelte';

  // PDF 工具整体按需加载：只看翻译页的用户不会下载这部分代码
  const app = import('./pdf/PdfApp.svelte');
</script>

{#await app}
  <div class="grid place-items-center py-24">
    <Logo class="size-9 animate-pulse" />
  </div>
{:then module}
  <module.default />
{:catch error}
  <div class="py-24 text-center">
    <p class="font-medium">PDF 工具加载失败</p>
    <p class="mt-1 text-[13px] text-muted">{String(error)}</p>
    <button class="btn btn-secondary mt-5" onclick={() => location.reload()}>刷新重试</button>
  </div>
{/await}
