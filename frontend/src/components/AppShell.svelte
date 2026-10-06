<script lang="ts">
  import type { Snippet } from 'svelte';
  import { api } from '../lib/api';
  import { AudioLines, ChevronDown, FileCog, Languages, LogOut, Monitor, Moon, Shield, Sun, UserRound } from '../lib/icons';
  import { router } from '../lib/router.svelte';
  import { session } from '../lib/session.svelte';
  import { theme } from '../lib/theme.svelte';
  import { toast } from '../lib/toast.svelte';
  import Logo from './Logo.svelte';
  import Menu from './Menu.svelte';

  let { children }: { children: Snippet } = $props();

  const ThemeIcon = $derived(theme.choice === 'light' ? Sun : theme.choice === 'dark' ? Moon : Monitor);
  const themeLabel = $derived(
    theme.choice === 'light' ? '浅色模式' : theme.choice === 'dark' ? '深色模式' : '跟随系统',
  );
  const initial = $derived((session.user?.display_name || session.user?.username || '?').slice(0, 1).toUpperCase());

  async function logout() {
    try {
      await api.logout();
    } catch (e) {
      toast.error(e);
    }
    session.signOut();
    router.go('/login', { replace: true });
  }

  // 文件拖到上传区以外松手时，浏览器会在当前标签直接打开它，已选的文件和正在处理的内容就丢了；
  // 没被拖放区接住的一律拦下（翻译、PDF 处理、会议记录等登录后的页面都在这个外壳里）
  function blockStrayDrop(e: DragEvent) {
    if (e.dataTransfer?.types.includes('Files')) e.preventDefault();
  }
</script>

<svelte:window ondragover={blockStrayDrop} ondrop={blockStrayDrop} />

<div class="flex min-h-dvh flex-col">
  <header class="sticky top-0 z-30 border-b border-line bg-page/80 backdrop-blur-md">
    <div class="mx-auto flex h-14 max-w-6xl items-center gap-2 px-4 sm:gap-5 sm:px-6">
      <a href="/" class="flex min-w-0 items-center gap-2.5 font-semibold tracking-tight">
        <Logo class="size-7 shrink-0" />
        <span class="truncate text-[15px]">{session.siteName}</span>
      </a>
      <nav class="flex items-center gap-1 text-[13.5px]">
        <a href="/" class="nav-link" class:active={router.path === '/'}>
          <Languages class="size-4" /><span class="hidden sm:inline">翻译</span>
        </a>
        <a href="/pdf" class="nav-link" class:active={router.path === '/pdf' || router.path.startsWith('/pdf/')}>
          <FileCog class="size-4" /><span class="hidden sm:inline">PDF 处理</span>
        </a>
        <a
          href="/meetings"
          class="nav-link"
          class:active={router.path === '/meetings' || router.path.startsWith('/meetings/')}
        >
          <AudioLines class="size-4" /><span class="hidden sm:inline">会议记录</span>
        </a>
        {#if session.isAdmin}
          <a href="/admin" class="nav-link" class:active={router.path.startsWith('/admin')}>
            <Shield class="size-4" /><span class="hidden sm:inline">管理后台</span>
          </a>
        {/if}
      </nav>
      <div class="ml-auto flex items-center gap-1">
        <button class="btn btn-ghost btn-icon" onclick={() => theme.cycle()} title="主题：{themeLabel}（点击切换）" aria-label="切换主题">
          <ThemeIcon class="size-[18px]" />
        </button>
        <Menu width="w-56">
          {#snippet trigger({ toggle, open })}
            <button class="flex items-center gap-2 rounded-full py-1 pr-2 pl-1 hover:bg-surface-2" aria-expanded={open} onclick={toggle}>
              <span class="grid size-7 place-items-center rounded-full bg-accent text-[13px] font-semibold text-white">{initial}</span>
              <span class="hidden max-w-32 truncate text-[13.5px] font-medium sm:block">{session.user?.display_name}</span>
              <ChevronDown class="size-3.5 text-muted" />
            </button>
          {/snippet}
          {#snippet children({ close })}
            <div class="px-2.5 pt-1.5 pb-2">
              <p class="truncate text-[13.5px] font-medium">{session.user?.display_name}</p>
              <p class="truncate text-[12px] text-muted">@{session.user?.username}{session.isAdmin ? ' · 管理员' : ''}</p>
            </div>
            <div class="menu-sep"></div>
            <a class="menu-item" href="/account" onclick={close}><UserRound class="size-4 text-muted" /> 账户设置</a>
            <button
              class="menu-item"
              onclick={() => {
                close();
                void logout();
              }}
            >
              <LogOut class="size-4 text-muted" /> 退出登录
            </button>
          {/snippet}
        </Menu>
      </div>
    </div>
  </header>

  <main class="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
    {@render children()}
  </main>

  <footer class="border-t border-line">
    <div class="mx-auto flex max-w-6xl flex-wrap items-center gap-x-3 gap-y-1 px-4 py-4 text-[12px] text-muted sm:px-6">
      <span>由 <a class="hover:text-ink-2 hover:underline" href="https://github.com/funstory-ai/BabelDOC" target="_blank" rel="noopener">BabelDOC</a> 驱动的私有部署</span>
      <span aria-hidden="true">·</span>
      <span>AGPL-3.0</span>
      <span class="ml-auto">v{session.meta?.version}{session.meta?.engine === 'mock' ? ' · 开发模式（模拟引擎）' : ''}</span>
    </div>
  </footer>
</div>
