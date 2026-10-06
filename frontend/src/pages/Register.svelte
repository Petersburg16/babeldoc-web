<script lang="ts">
  import AuthLayout from '../components/AuthLayout.svelte';
  import { api } from '../lib/api';
  import { errorText } from '../lib/format';
  import { LoaderCircle, Lock } from '../lib/icons';
  import { router } from '../lib/router.svelte';
  import { session } from '../lib/session.svelte';

  let invite = $state(router.query.get('code') ?? '');
  let username = $state('');
  let displayName = $state('');
  let password = $state('');
  let confirmPassword = $state('');
  let error = $state('');
  let loading = $state(false);

  const mode = $derived(session.meta?.registration ?? 'invite');

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    error = '';
    if (password !== confirmPassword) {
      error = '两次输入的密码不一致';
      return;
    }
    loading = true;
    try {
      session.me = await api.register({
        username: username.trim(),
        password,
        display_name: displayName.trim(),
        invite_code: invite.trim(),
      });
      router.go('/', { replace: true });
    } catch (e) {
      error = errorText(e);
    } finally {
      loading = false;
    }
  }
</script>

<AuthLayout subtitle={mode === 'invite' ? '凭邀请码创建账号' : '创建账号'}>
  {#if mode === 'closed'}
    <div class="flex flex-col items-center py-4 text-center">
      <Lock class="size-8 text-muted" strokeWidth={1.5} />
      <p class="mt-3 font-medium">当前未开放注册</p>
      <p class="mt-1 text-[13px] text-muted">请联系管理员为你开通账号。</p>
    </div>
  {:else}
    <form class="space-y-4" onsubmit={submit}>
      {#if mode === 'invite'}
        <div>
          <label class="label" for="invite">邀请码</label>
          <input id="invite" class="field font-mono tracking-wider uppercase" required bind:value={invite} placeholder="向管理员索取" />
        </div>
      {/if}
      <div>
        <label class="label" for="username">用户名</label>
        <input
          id="username"
          class="field"
          autocomplete="username"
          required
          minlength={3}
          maxlength={32}
          pattern="[A-Za-z0-9_.\-]+"
          bind:value={username}
        />
        <p class="hint">3–32 位，字母、数字、下划线、点或短横线，用于登录</p>
      </div>
      <div>
        <label class="label" for="display">昵称 <span class="font-normal text-muted">（可选）</span></label>
        <input id="display" class="field" maxlength={64} bind:value={displayName} />
      </div>
      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label class="label" for="password">密码</label>
          <input id="password" class="field" type="password" autocomplete="new-password" required minlength={8} bind:value={password} />
        </div>
        <div>
          <label class="label" for="confirm">确认密码</label>
          <input id="confirm" class="field" type="password" autocomplete="new-password" required minlength={8} bind:value={confirmPassword} />
        </div>
      </div>
      <p class="hint !mt-1">密码至少 8 位</p>
      {#if error}
        <p class="rounded-lg bg-bad-soft px-3 py-2 text-[13px] text-bad-ink" role="alert">{error}</p>
      {/if}
      <button class="btn btn-primary btn-lg w-full" disabled={loading}>
        {#if loading}<LoaderCircle class="size-4 animate-spin" />{/if}
        创建账号
      </button>
    </form>
  {/if}

  {#snippet below()}
    <p class="mt-5 text-center text-[13px] text-muted">
      已有账号？<a href="/login" class="font-medium text-accent hover:underline">直接登录</a>
    </p>
  {/snippet}
</AuthLayout>
