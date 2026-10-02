<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../../lib/api';
  import { confirm } from '../../lib/confirm.svelte';
  import { copyText, dateTime, relativeTime } from '../../lib/format';
  import { Ban, Copy, Info, Link, LoaderCircle, Ticket, Trash2 } from '../../lib/icons';
  import { session } from '../../lib/session.svelte';
  import { toast } from '../../lib/toast.svelte';
  import type { Invite } from '../../lib/types';

  let invites = $state<Invite[]>([]);
  let loading = $state(true);
  let note = $state('');
  let maxUses = $state(1);
  let days = $state<number | null>(7);
  let creating = $state(false);

  const stateStyles = {
    active: { label: '可用', cls: 'bg-good-soft text-good-ink' },
    used: { label: '已用完', cls: 'bg-surface-2 text-muted' },
    expired: { label: '已过期', cls: 'bg-surface-2 text-muted' },
    revoked: { label: '已作废', cls: 'bg-bad-soft text-bad-ink' },
  };

  async function load() {
    try {
      invites = await api.admin.invites();
    } catch (e) {
      toast.error(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  function link(invite: Invite) {
    return `${location.origin}/register?code=${invite.code}`;
  }

  async function create(event: SubmitEvent) {
    event.preventDefault();
    creating = true;
    try {
      const invite = await api.admin.createInvite({ note: note.trim(), max_uses: maxUses, expires_in_days: days });
      invites = [invite, ...invites];
      note = '';
      if (await copyText(link(invite))) toast.success('已生成，注册链接已复制到剪贴板');
    } catch (e) {
      toast.error(e);
    } finally {
      creating = false;
    }
  }

  async function copy(text: string, what: string) {
    if (await copyText(text)) toast.success(`已复制${what}`);
  }

  async function revoke(invite: Invite) {
    try {
      const updated = await api.admin.revokeInvite(invite.id);
      invites = invites.map((i) => (i.id === invite.id ? updated : i));
    } catch (e) {
      toast.error(e);
    }
  }

  async function remove(invite: Invite) {
    if (!(await confirm({ title: '删除这条邀请码记录？', confirmText: '删除', danger: true }))) return;
    try {
      await api.admin.deleteInvite(invite.id);
      invites = invites.filter((i) => i.id !== invite.id);
    } catch (e) {
      toast.error(e);
    }
  }
</script>

<div class="space-y-5">
  {#if session.meta && session.meta.registration !== 'invite'}
    <div class="flex gap-2.5 rounded-xl bg-warn-soft px-4 py-3 text-[13px] text-warn-ink">
      <Info class="mt-0.5 size-4 shrink-0" />
      <p>
        当前注册方式是「{session.meta.registration === 'open' ? '开放注册' : '关闭注册'}」，邀请码暂不生效。
        <a href="/admin/settings" class="font-medium underline">去系统设置修改</a>
      </p>
    </div>
  {/if}

  <form class="card grid gap-4 p-5 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end" onsubmit={create}>
    <div>
      <label class="label" for="inv-note">备注</label>
      <input id="inv-note" class="field" maxlength={255} placeholder="例如：给课题组的小王" bind:value={note} />
    </div>
    <div>
      <label class="label" for="inv-uses">可注册人数</label>
      <input id="inv-uses" class="field" type="number" min="1" max="1000" bind:value={maxUses} />
    </div>
    <div>
      <label class="label" for="inv-days">有效期</label>
      <select id="inv-days" class="field" bind:value={days}>
        <option value={1}>1 天</option>
        <option value={7}>7 天</option>
        <option value={30}>30 天</option>
        <option value={null}>永久</option>
      </select>
    </div>
    <button class="btn btn-primary" disabled={creating}>
      {#if creating}<LoaderCircle class="size-4 animate-spin" />{:else}<Ticket class="size-4" />{/if}生成邀请码
    </button>
  </form>

  {#if invites.length}
    <div class="grid gap-3 md:grid-cols-2">
      {#each invites as invite (invite.id)}
        {@const st = stateStyles[invite.state]}
        <div class="card p-4 {invite.state === 'active' ? '' : 'opacity-70'}">
          <div class="flex items-center gap-2">
            <span class="font-mono text-[17px] font-semibold tracking-[0.12em]">{invite.code}</span>
            <span class="rounded-full px-2 py-0.5 text-[11.5px] font-medium {st.cls}">{st.label}</span>
            <div class="ml-auto flex gap-1">
              {#if invite.state === 'active'}
                <button class="btn btn-ghost btn-sm btn-icon" title="复制注册链接" aria-label="复制注册链接" onclick={() => copy(link(invite), '注册链接')}><Link class="size-4" /></button>
                <button class="btn btn-ghost btn-sm btn-icon" title="复制邀请码" aria-label="复制邀请码" onclick={() => copy(invite.code, '邀请码')}><Copy class="size-4" /></button>
                <button class="btn btn-ghost btn-sm btn-icon" title="作废" aria-label="作废" onclick={() => revoke(invite)}><Ban class="size-4" /></button>
              {:else}
                <button class="btn btn-ghost btn-sm btn-icon text-bad-ink" title="删除记录" aria-label="删除记录" onclick={() => remove(invite)}><Trash2 class="size-4" /></button>
              {/if}
            </div>
          </div>
          <p class="mt-2 text-[12.5px] text-muted">
            已注册 {invite.used_count}/{invite.max_uses} 人 ·
            {#if !invite.expires_at}永久有效{:else if new Date(invite.expires_at) > new Date()}{dateTime(invite.expires_at)} 到期{:else}已过期{/if}
            · {relativeTime(invite.created_at)}创建
          </p>
          {#if invite.note}<p class="mt-1 truncate text-[13px] text-ink-2">{invite.note}</p>{/if}
        </div>
      {/each}
    </div>
  {:else if !loading}
    <div class="card flex flex-col items-center px-6 py-12 text-center">
      <Ticket class="size-7 text-muted" strokeWidth={1.5} />
      <p class="mt-3 font-medium">还没有邀请码</p>
      <p class="mt-1 text-[13px] text-muted">生成后把注册链接发给朋友，对方打开即可注册。</p>
    </div>
  {/if}
</div>
