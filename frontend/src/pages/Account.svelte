<script lang="ts">
  import ProgressBar from '../components/ProgressBar.svelte';
  import { api } from '../lib/api';
  import { compact } from '../lib/format';
  import { ChartColumn, KeyRound, LoaderCircle, UserRound } from '../lib/icons';
  import { session } from '../lib/session.svelte';
  import { toast } from '../lib/toast.svelte';

  let displayName = $state(session.user?.display_name ?? '');
  let savingProfile = $state(false);
  let oldPassword = $state('');
  let newPassword = $state('');
  let confirmPassword = $state('');
  let savingPassword = $state(false);

  const usage = $derived(session.me?.usage);

  async function saveProfile(event: SubmitEvent) {
    event.preventDefault();
    savingProfile = true;
    try {
      session.me = await api.updateProfile(displayName.trim());
      toast.success('昵称已更新');
    } catch (e) {
      toast.error(e);
    } finally {
      savingProfile = false;
    }
  }

  async function savePassword(event: SubmitEvent) {
    event.preventDefault();
    if (newPassword !== confirmPassword) {
      toast.error('两次输入的新密码不一致');
      return;
    }
    savingPassword = true;
    try {
      await api.changePassword(oldPassword, newPassword);
      oldPassword = newPassword = confirmPassword = '';
      toast.success('密码已修改，其他设备上的登录已失效');
    } catch (e) {
      toast.error(e);
    } finally {
      savingPassword = false;
    }
  }
</script>

<div class="mx-auto max-w-3xl space-y-6">
  <div>
    <h1 class="text-[22px] font-semibold tracking-tight">账户设置</h1>
    <p class="mt-1 text-[13.5px] text-muted">管理你的资料、密码和用量。</p>
  </div>

  {#if usage}
    <section class="card p-5">
      <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><ChartColumn class="size-4 text-accent" />本月用量</h2>
      <div class="mt-4 grid gap-4 sm:grid-cols-3">
        <div>
          <p class="text-[12.5px] text-muted">已翻译页数</p>
          <p class="mt-1 text-2xl font-semibold">{usage.month_pages}<span class="ml-1 text-sm font-normal text-muted">/ {usage.quota || '不限'}</span></p>
        </div>
        <div>
          <p class="text-[12.5px] text-muted">消耗 tokens</p>
          <p class="mt-1 text-2xl font-semibold">{compact(usage.month_tokens)}</p>
        </div>
        <div>
          <p class="text-[12.5px] text-muted">历史任务</p>
          <p class="mt-1 text-2xl font-semibold">{usage.total_jobs}</p>
        </div>
      </div>
      {#if usage.quota}
        <div class="mt-4">
          <ProgressBar value={(usage.month_pages / usage.quota) * 100} label="本月额度使用" />
          <p class="hint">额度每月 1 日重置，失败或取消的任务不计入。</p>
        </div>
      {/if}
    </section>
  {/if}

  <section class="card p-5">
    <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><UserRound class="size-4 text-accent" />个人资料</h2>
    <form class="mt-4 grid gap-4 sm:grid-cols-2" onsubmit={saveProfile}>
      <div>
        <label class="label" for="acc-username">用户名</label>
        <input id="acc-username" class="field" value={session.user?.username} disabled />
      </div>
      <div>
        <label class="label" for="acc-display">昵称</label>
        <input id="acc-display" class="field" maxlength={64} bind:value={displayName} />
      </div>
      <div class="sm:col-span-2">
        <button class="btn btn-primary" disabled={savingProfile}>
          {#if savingProfile}<LoaderCircle class="size-4 animate-spin" />{/if}保存
        </button>
      </div>
    </form>
  </section>

  <section class="card p-5">
    <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><KeyRound class="size-4 text-accent" />修改密码</h2>
    <form class="mt-4 grid gap-4 sm:grid-cols-3" onsubmit={savePassword}>
      <div>
        <label class="label" for="pw-old">当前密码</label>
        <input id="pw-old" class="field" type="password" autocomplete="current-password" required bind:value={oldPassword} />
      </div>
      <div>
        <label class="label" for="pw-new">新密码</label>
        <input id="pw-new" class="field" type="password" autocomplete="new-password" minlength={8} required bind:value={newPassword} />
      </div>
      <div>
        <label class="label" for="pw-confirm">确认新密码</label>
        <input id="pw-confirm" class="field" type="password" autocomplete="new-password" minlength={8} required bind:value={confirmPassword} />
      </div>
      <div class="sm:col-span-3">
        <button class="btn btn-primary" disabled={savingPassword}>
          {#if savingPassword}<LoaderCircle class="size-4 animate-spin" />{/if}修改密码
        </button>
      </div>
    </form>
  </section>
</div>
