<script lang="ts">
  import LazyRoute from '../components/LazyRoute.svelte';
</script>

<!-- PDF 工具整体按需加载：只看翻译页的用户不会下载这部分代码 -->
<LazyRoute load={() => import('./pdf/PdfApp.svelte')} label="PDF 工具" />
