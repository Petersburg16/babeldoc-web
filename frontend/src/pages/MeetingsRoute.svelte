<script lang="ts">
  import LazyRoute from '../components/LazyRoute.svelte';
</script>

<!-- 会议记录的页面和依赖（Markdown 渲染等）单独打包，打开这个标签时才下载 -->
<LazyRoute load={() => import('./meetings/MeetingsApp.svelte')} label="会议记录" />
