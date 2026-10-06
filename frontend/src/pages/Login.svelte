<script lang="ts">
  import AuthLayout from '../components/AuthLayout.svelte';
  import { api } from '../lib/api';
  import { errorText } from '../lib/format';
  import { BookOpenText, Languages, LoaderCircle, LogIn, Sparkles, TriangleAlert } from '../lib/icons';
  import { router } from '../lib/router.svelte';
  import { session } from '../lib/session.svelte';

  let username = $state('');
  let password = $state('');
  let error = $state('');
  let loading = $state(false);

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    error = '';
    loading = true;
    try {
      session.me = await api.login(username.trim(), password);
      const next = router.query.get('next');
      router.go(next && next.startsWith('/') && !next.startsWith('//') ? next : '/', { replace: true });
    } catch (e) {
      error = errorText(e);
    } finally {
      loading = false;
    }
  }
</script>

<AuthLayout subtitle="学术论文 PDF 翻译 · 保留原版排版">
  {#if session.meta?.needs_setup}
    <div class="mb-5 flex gap-2 rounded-xl bg-warn-soft px-3 py-2.5 text-[12.5px] leading-relaxed text-warn-ink">
      <TriangleAlert class="mt-0.5 size-4 shrink-0" />
      <p>
        还没有管理员账号。请在服务器的 <span class="font-mono">backend</span> 目录运行
        <span class="kbd mt-1 inline-block break-all">uv run python -m app.cli create-admin 用户名</span>
      </p>
    </div>
  {/if}
  <form class="space-y-4" onsubmit={submit}>
    <div>
      <label class="label" for="username">用户名</label>
      <input id="username" class="field" autocomplete="username" required bind:value={username} />
    </div>
    <div>
      <label class="label" for="password">密码</label>
      <input id="password" class="field" type="password" autocomplete="current-password" required bind:value={password} />
    </div>
    {#if error}
      <p class="rounded-lg bg-bad-soft px-3 py-2 text-[13px] text-bad-ink" role="alert">{error}</p>
    {/if}
    <button class="btn btn-primary btn-lg w-full" disabled={loading}>
      {#if loading}<LoaderCircle class="size-4 animate-spin" />{:else}<LogIn class="size-4" />{/if}
      登录
    </button>
  </form>

  {#snippet below()}
    {#if session.meta?.registration !== 'closed'}
      <p class="mt-5 text-center text-[13px] text-muted">
        还没有账号？<a href="/register" class="font-medium text-accent hover:underline">
          {session.meta?.registration === 'invite' ? '使用邀请码注册' : '注册'}
        </a>
      </p>
    {/if}
    <ul class="mt-8 grid grid-cols-3 gap-2 text-center text-[12px] text-muted">
      <li class="flex flex-col items-center gap-1.5"><Languages class="size-4 text-ink-2" />双语对照输出</li>
      <li class="flex flex-col items-center gap-1.5"><Sparkles class="size-4 text-ink-2" />公式图表原样保留</li>
      <li class="flex flex-col items-center gap-1.5"><BookOpenText class="size-4 text-ink-2" />自动提取术语</li>
    </ul>
  {/snippet}
</AuthLayout>
