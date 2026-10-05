<script lang="ts">
  import { router } from '../../lib/router.svelte';
  import MeetingDetailPage from './MeetingDetailPage.svelte';
  import MeetingListPage from './MeetingListPage.svelte';

  // /meetings 是列表和上传，/meetings/<id> 是详情
  const id = $derived(router.path.replace(/\/+$/, '').split('/')[2] ?? '');

  function blockStrayDrop(e: DragEvent) {
    // 文件拖到上传区外面时，别让浏览器直接打开它
    if (e.dataTransfer?.types.includes('Files')) e.preventDefault();
  }
</script>

<svelte:window ondragover={blockStrayDrop} ondrop={blockStrayDrop} />

{#if id}
  {#key id}<MeetingDetailPage {id} />{/key}
{:else}
  <MeetingListPage />
{/if}
