<script lang="ts">
  import { onMount } from 'svelte';
  import AppShell from './components/AppShell.svelte';
  import ConfirmHost from './components/ConfirmHost.svelte';
  import Logo from './components/Logo.svelte';
  import Toasts from './components/Toasts.svelte';
  import { onUnauthorized } from './lib/api';
  import { jobs } from './lib/jobs.svelte';
  import { interceptLinks, router } from './lib/router.svelte';
  import { session } from './lib/session.svelte';
  import Account from './pages/Account.svelte';
  import Admin from './pages/admin/Admin.svelte';
  import Home from './pages/Home.svelte';
  import Login from './pages/Login.svelte';
  import NotFound from './pages/NotFound.svelte';
  import PdfRoute from './pages/PdfRoute.svelte';
  import Register from './pages/Register.svelte';

  const PUBLIC = new Set(['/login', '/register']);

  onUnauthorized(() => {
    session.me = null;
    jobs.disconnect();
  });

  onMount(() => {
    void session.init();
  });

  $effect(() => {
    if (!session.ready || session.error) return;
    const path = router.path;
    if (!session.me && !PUBLIC.has(path)) {
      const next = path === '/' ? '' : `?next=${encodeURIComponent(path + router.search)}`;
      router.go(`/login${next}`, { replace: true });
    } else if (session.me && PUBLIC.has(path)) {
      router.go('/', { replace: true });
    } else if (session.me && path.startsWith('/admin') && !session.isAdmin) {
      router.go('/', { replace: true });
    }
  });

  $effect(() => {
    document.title = session.meta?.site_name ?? 'BabelDOC Web';
  });

  const Page = $derived.by(() => {
    const path = router.path.replace(/\/+$/, '') || '/';
    if (path === '/login') return Login;
    if (path === '/register') return Register;
    if (path === '/') return Home;
    if (path === '/account') return Account;
    if (path === '/pdf' || path.startsWith('/pdf/')) return PdfRoute;
    if (path === '/admin' || path.startsWith('/admin/')) return Admin;
    return NotFound;
  });
  const isPublic = $derived(PUBLIC.has(router.path));
</script>

<svelte:document onclick={interceptLinks} />

{#if !session.ready}
  <div class="grid min-h-dvh place-items-center">
    <Logo class="size-10 animate-pulse" />
  </div>
{:else if session.error}
  <div class="grid min-h-dvh place-items-center px-4 text-center">
    <div>
      <Logo class="mx-auto size-10" />
      <p class="mt-4 font-medium">无法连接到服务器</p>
      <p class="mt-1 text-[13px] text-muted">{session.error}</p>
      <button class="btn btn-secondary mt-5" onclick={() => location.reload()}>重试</button>
    </div>
  </div>
{:else if isPublic}
  <Page />
{:else if session.me}
  <AppShell><Page /></AppShell>
{/if}

<Toasts />
<ConfirmHost />
