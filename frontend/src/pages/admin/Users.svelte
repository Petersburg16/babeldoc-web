<script lang="ts">
  import { onMount } from 'svelte';
  import Modal from '../../components/Modal.svelte';
  import Switch from '../../components/Switch.svelte';
  import { api } from '../../lib/api';
  import { confirm } from '../../lib/confirm.svelte';
  import { copyText, dateTime, relativeTime } from '../../lib/format';
  import { Copy, KeyRound, LoaderCircle, Pencil, Plus, Trash2 } from '../../lib/icons';
  import { session } from '../../lib/session.svelte';
  import { toast } from '../../lib/toast.svelte';
  import type { AdminUser, Role } from '../../lib/types';

  interface Draft {
    id: number | null;
    username: string;
    display_name: string;
    password: string;
    role: Role;
    quota: string;
    is_active: boolean;
    note: string;
  }

  let users = $state<AdminUser[]>([]);
  let loading = $state(true);
  let draft = $state<Draft | null>(null);
  let saving = $state(false);
  let revealed = $state<{ username: string; password: string } | null>(null);

  async function load() {
    try {
      users = await api.admin.users();
    } catch (e) {
      toast.error(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  function randomPassword() {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';
    const values = crypto.getRandomValues(new Uint32Array(12));
    return Array.from(values, (v) => chars[v % chars.length]).join('');
  }

  function openCreate() {
    draft = { id: null, username: '', display_name: '', password: randomPassword(), role: 'user', quota: '', is_active: true, note: '' };
  }

  function openEdit(user: AdminUser) {
    draft = {
      id: user.id,
      username: user.username,
      display_name: user.display_name,
      password: '',
      role: user.role,
      quota: user.page_quota === null ? '' : String(user.page_quota),
      is_active: user.is_active,
      note: user.note,
    };
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (!draft) return;
    const quota = draft.quota.trim() === '' ? null : Number(draft.quota);
    if (quota !== null && (!Number.isInteger(quota) || quota < 0)) {
      toast.error('额度需要是不小于 0 的整数');
      return;
    }
    saving = true;
    try {
      if (draft.id === null) {
        await api.admin.createUser({
          username: draft.username.trim(),
          password: draft.password,
          display_name: draft.display_name.trim(),
          role: draft.role,
          page_quota: quota,
          note: draft.note,
        });
        revealed = { username: draft.username.trim().toLowerCase(), password: draft.password };
        toast.success('用户已创建');
      } else {
        await api.admin.patchUser(draft.id, {
          display_name: draft.display_name.trim(),
          role: draft.role,
          is_active: draft.is_active,
          page_quota: quota,
          note: draft.note,
        });
        toast.success('已保存');
      }
      draft = null;
      await load();
    } catch (e) {
      toast.error(e);
    } finally {
      saving = false;
    }
  }

  async function resetPassword(user: AdminUser) {
    const ok = await confirm({
      title: `重置 @${user.username} 的密码？`,
      message: '会生成一个新的随机密码，并让该用户在所有设备上退出登录。',
      confirmText: '重置',
    });
    if (!ok) return;
    try {
      const { password } = await api.admin.resetPassword(user.id);
      revealed = { username: user.username, password };
    } catch (e) {
      toast.error(e);
    }
  }

  async function remove(user: AdminUser) {
    const ok = await confirm({
      title: `删除 @${user.username}？`,
      message: `该用户的 ${user.total_jobs} 个任务和所有文件会被一并删除，无法恢复。只想禁止登录的话，用“编辑 → 停用”即可。`,
      confirmText: '永久删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.admin.deleteUser(user.id);
      toast.success('已删除');
      await load();
    } catch (e) {
      toast.error(e);
    }
  }

  async function copyCredentials() {
    if (!revealed) return;
    const origin = location.origin;
    const ok = await copyText(`地址：${origin}\n用户名：${revealed.username}\n密码：${revealed.password}`);
    if (ok) toast.success('已复制登录信息');
  }
</script>

<div class="space-y-4">
  <div class="flex items-center gap-3">
    <p class="text-[13px] text-muted">共 {users.length} 个用户</p>
    <button class="btn btn-primary btn-sm ml-auto" onclick={openCreate}><Plus class="size-4" />新建用户</button>
  </div>

  <div class="card overflow-x-auto">
    <table class="table min-w-[820px]">
      <thead>
        <tr><th>用户</th><th>角色</th><th>本月页数</th><th class="text-right">任务</th><th>最近登录</th><th>注册时间</th><th class="text-right">操作</th></tr>
      </thead>
      <tbody>
        {#each users as user (user.id)}
          {@const ratio = user.effective_quota ? Math.min(1, user.month_pages / user.effective_quota) : 0}
          <tr class={user.is_active ? '' : 'opacity-55'}>
            <td>
              <div class="flex items-center gap-2.5">
                <span class="grid size-8 shrink-0 place-items-center rounded-full bg-surface-3 text-[13px] font-semibold text-ink-2">
                  {(user.display_name || user.username).slice(0, 1).toUpperCase()}
                </span>
                <div class="min-w-0">
                  <p class="truncate font-medium">
                    {user.display_name}
                    {#if !user.is_active}<span class="ml-1 rounded bg-surface-3 px-1.5 py-px text-[11px] font-normal text-muted">已停用</span>{/if}
                  </p>
                  <p class="truncate text-[12px] text-muted">@{user.username}{user.note ? ` · ${user.note}` : ''}</p>
                </div>
              </div>
            </td>
            <td>
              {#if user.role === 'admin'}
                <span class="rounded-full bg-accent-soft px-2 py-0.5 text-[12px] font-medium text-accent-ink">管理员</span>
              {:else}
                <span class="text-ink-2">用户</span>
              {/if}
            </td>
            <td class="min-w-40">
              <p class="tabular text-[12.5px]">
                {user.month_pages}
                <span class="text-muted">/ {user.effective_quota || '不限'}{user.page_quota === null && user.role !== 'admin' ? '（默认）' : ''}</span>
              </p>
              {#if user.effective_quota}
                <div class="mt-1 h-1.5 w-28 overflow-hidden rounded-full bg-accent-track/60">
                  <div class="h-full rounded-full {ratio >= 0.95 ? 'bg-bad' : ratio >= 0.8 ? 'bg-warn' : 'bg-accent'}" style="width: {ratio * 100}%"></div>
                </div>
              {/if}
            </td>
            <td class="tabular text-right">{user.total_jobs}</td>
            <td class="whitespace-nowrap text-ink-2">{user.last_login_at ? relativeTime(user.last_login_at) : '从未'}</td>
            <td class="whitespace-nowrap text-ink-2">{dateTime(user.created_at)}</td>
            <td>
              <div class="flex justify-end gap-1">
                <button class="btn btn-ghost btn-sm btn-icon" title="编辑" aria-label="编辑" onclick={() => openEdit(user)}><Pencil class="size-4" /></button>
                <button class="btn btn-ghost btn-sm btn-icon" title="重置密码" aria-label="重置密码" onclick={() => resetPassword(user)}><KeyRound class="size-4" /></button>
                {#if user.id !== session.user?.id}
                  <button class="btn btn-ghost btn-sm btn-icon text-bad-ink" title="删除" aria-label="删除" onclick={() => remove(user)}><Trash2 class="size-4" /></button>
                {/if}
              </div>
            </td>
          </tr>
        {:else}
          <tr><td colspan="7" class="py-12 text-center text-muted">{loading ? '加载中…' : '还没有用户'}</td></tr>
        {/each}
      </tbody>
    </table>
  </div>
</div>

<Modal open={!!draft} title={draft?.id === null ? '新建用户' : `编辑 @${draft?.username}`} onclose={() => (draft = null)}>
  {#if draft}
    <form id="user-form" class="space-y-4" onsubmit={save}>
      {#if draft.id === null}
        <div class="grid gap-4 sm:grid-cols-2">
          <div>
            <label class="label" for="u-name">用户名</label>
            <input id="u-name" class="field" required minlength={3} maxlength={32} bind:value={draft.username} />
          </div>
          <div>
            <label class="label" for="u-pass">初始密码</label>
            <div class="flex gap-2">
              <input id="u-pass" class="field font-mono" required minlength={8} bind:value={draft.password} />
              <button type="button" class="btn btn-secondary shrink-0" onclick={() => draft && (draft.password = randomPassword())}>换一个</button>
            </div>
          </div>
        </div>
      {/if}
      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label class="label" for="u-display">昵称</label>
          <input id="u-display" class="field" maxlength={64} bind:value={draft.display_name} placeholder="默认同用户名" />
        </div>
        <div>
          <label class="label" for="u-role">角色</label>
          <select id="u-role" class="field" bind:value={draft.role} disabled={draft.id === session.user?.id}>
            <option value="user">普通用户</option>
            <option value="admin">管理员</option>
          </select>
        </div>
      </div>
      <div>
        <label class="label" for="u-quota">每月页数额度</label>
        <input id="u-quota" class="field" inputmode="numeric" placeholder="留空 = 跟随系统默认" bind:value={draft.quota} />
        <p class="hint">留空跟随系统默认额度；填 0 表示不限。管理员始终不限。</p>
      </div>
      <div>
        <label class="label" for="u-note">备注</label>
        <input id="u-note" class="field" maxlength={255} bind:value={draft.note} placeholder="例如：实验室同学" />
      </div>
      {#if draft.id !== null && draft.id !== session.user?.id}
        <Switch bind:checked={draft.is_active} label="允许登录" description="关闭后该用户立即退出登录，排队中的任务不受影响" />
      {/if}
    </form>
  {/if}
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={() => (draft = null)}>取消</button>
    <button class="btn btn-primary" form="user-form" disabled={saving}>
      {#if saving}<LoaderCircle class="size-4 animate-spin" />{/if}保存
    </button>
  {/snippet}
</Modal>

<Modal open={!!revealed} title="登录信息" description="密码只显示这一次，请复制后发给对方" size="sm" onclose={() => (revealed = null)}>
  {#if revealed}
    <dl class="space-y-2 rounded-xl bg-surface-2 p-3.5 text-[13px]">
      <div class="flex justify-between gap-3"><dt class="text-muted">用户名</dt><dd class="font-mono">{revealed.username}</dd></div>
      <div class="flex justify-between gap-3"><dt class="text-muted">密码</dt><dd class="font-mono font-semibold">{revealed.password}</dd></div>
    </dl>
  {/if}
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={() => (revealed = null)}>完成</button>
    <button class="btn btn-primary" onclick={copyCredentials}><Copy class="size-4" />复制登录信息</button>
  {/snippet}
</Modal>
