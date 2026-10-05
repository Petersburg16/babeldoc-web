<script lang="ts">
  import Logo from '../components/Logo.svelte';

  // 会议记录的页面和依赖（Markdown 渲染等）单独打包，打开这个标签时才下载
  const app = import('./meetings/MeetingsApp.svelte');
</script>

{#await app}
  <div class="grid place-items-center py-24"><Logo class="size-9 animate-pulse" /></div>
{:then module}
  <module.default />
{:catch error}
  <div class="grid place-items-center py-24 text-center">
    <div>
      <p class="font-medium">会议记录加载失败</p>
      <p class="mt-1 text-[13px] text-muted">{error?.message ?? ''}</p>
      <button class="btn btn-secondary mt-5" onclick={() => location.reload()}>刷新重试</button>
    </div>
  </div>
{/await}
