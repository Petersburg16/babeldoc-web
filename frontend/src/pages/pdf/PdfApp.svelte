<script lang="ts">
  import { router } from '../../lib/router.svelte';
  import { findTool } from '../../lib/pdf/tools';
  import NotFound from '../NotFound.svelte';
  import PdfHome from './PdfHome.svelte';
  import ToolPage from './ToolPage.svelte';
  import PasswordHost from './ui/PasswordHost.svelte';

  const slug = $derived(router.path.replace(/\/+$/, '').split('/')[2] ?? '');
  const tool = $derived(slug ? findTool(slug) : undefined);
</script>

{#if !slug}
  <PdfHome />
{:else if tool}
  {#key tool.id}
    <ToolPage {tool} />
  {/key}
{:else}
  <NotFound />
{/if}

<PasswordHost />
