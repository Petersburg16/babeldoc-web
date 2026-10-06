<script lang="ts">
  import { router } from '../../lib/router.svelte';
  import MeetingDetailPage from './MeetingDetailPage.svelte';
  import MeetingListPage from './MeetingListPage.svelte';

  // /meetings 是列表和上传，/meetings/<id> 是详情
  const id = $derived(router.path.replace(/\/+$/, '').split('/')[2] ?? '');
</script>

{#if id}
  {#key id}<MeetingDetailPage {id} />{/key}
{:else}
  <MeetingListPage />
{/if}
