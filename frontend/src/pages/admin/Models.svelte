<script lang="ts">
  import { onMount } from 'svelte';
  import EmptyState from '../../components/admin/EmptyState.svelte';
  import EntityCard from '../../components/admin/EntityCard.svelte';
  import ResultNote from '../../components/admin/ResultNote.svelte';
  import { api } from '../../lib/api';
  import { confirm } from '../../lib/confirm.svelte';
  import { errorText } from '../../lib/format';
  import { Bot, LoaderCircle, Pencil, Plus, RefreshCw, Star, Trash2, Info } from '../../lib/icons';
  import { toast } from '../../lib/toast.svelte';
  import type { ModelAdmin, ModelTest } from '../../lib/types';
  import TranslationModelModal from './TranslationModelModal.svelte';

  let models = $state<ModelAdmin[]>([]);
  let loading = $state(true);
  /** 'new' 表示新建 */
  let editing = $state<ModelAdmin | 'new' | null>(null);
  let tests = $state<Record<number, ModelTest | 'running'>>({});

  async function load() {
    try {
      models = await api.admin.models();
    } catch (e) {
      toast.error(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  async function saved(message: string) {
    editing = null;
    toast.success(message);
    await load();
  }

  function testText(r: ModelTest) {
    return r.ok
      ? `连接正常 · ${((r.latency_ms ?? 0) / 1000).toFixed(1)} 秒 · 回复：${r.reply || '（空）'}`
      : `${r.status ? `HTTP ${r.status} · ` : ''}${r.error ?? ''}`;
  }

  async function test(m: ModelAdmin) {
    tests[m.id] = 'running';
    try {
      tests[m.id] = await api.admin.testModel(m.id);
    } catch (e) {
      tests[m.id] = { ok: false, error: errorText(e), latency_ms: null, status: null, reply: null };
    }
  }

  async function quickPatch(m: ModelAdmin, body: Record<string, unknown>) {
    try {
      await api.admin.patchModel(m.id, body);
      await load();
    } catch (e) {
      toast.error(e);
    }
  }

  async function remove(m: ModelAdmin) {
    const ok = await confirm({
      title: `删除模型「${m.name}」？`,
      message: '已有任务的记录会保留模型名称；排队中使用该模型的任务会失败，可重试改用默认模型。',
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.admin.deleteModel(m.id);
      toast.success('已删除');
      await load();
    } catch (e) {
      toast.error(e);
    }
  }
</script>

<div class="space-y-4">
  <div class="flex flex-wrap items-center gap-3">
    <p class="flex items-center gap-1.5 text-[13px] text-muted">
      <Info class="size-4" />支持任何 OpenAI 兼容接口（含中转站）。API Key 加密保存在服务器，不会发送到浏览器。
    </p>
    <button class="btn btn-primary btn-sm ml-auto" onclick={() => (editing = 'new')}><Plus class="size-4" />添加模型</button>
  </div>

  {#if models.length}
    <div class="grid gap-4 lg:grid-cols-2">
      {#each models as m (m.id)}
        {@const result = tests[m.id]}
        <EntityCard icon={Bot} name={m.name} description={m.description} enabled={m.enabled} isDefault={m.is_default}>
          {#snippet details()}
            <dt class="text-muted">接口地址</dt>
            <dd class="truncate font-mono text-[12px]" title={m.base_url}>{m.base_url || 'https://api.openai.com/v1'}</dd>
            <dt class="text-muted">模型</dt>
            <dd class="truncate font-mono text-[12px]">{m.model}{m.term_model ? ` · 术语 ${m.term_model}` : ''}</dd>
            <dt class="text-muted">API Key</dt>
            <dd class="font-mono text-[12px]">{m.api_key_set ? m.api_key_masked : '未设置'}</dd>
            <dt class="text-muted">并发</dt>
            <dd>每秒 {m.qps} 个请求{m.pool_max_workers ? ` · ${m.pool_max_workers} 线程` : ''}</dd>
          {/snippet}
          {#snippet notes()}
            {#if result && result !== 'running'}
              <ResultNote ok={result.ok} text={testText(result)} />
            {/if}
          {/snippet}
          {#snippet actions()}
            <button class="btn btn-secondary btn-sm" disabled={result === 'running'} onclick={() => test(m)}>
              {#if result === 'running'}<LoaderCircle class="size-3.5 animate-spin" />{:else}<RefreshCw class="size-3.5" />{/if}
              测试连接
            </button>
            <button class="btn btn-ghost btn-sm" onclick={() => (editing = m)}><Pencil class="size-3.5" />编辑</button>
            {#if !m.is_default && m.enabled}
              <button class="btn btn-ghost btn-sm" onclick={() => quickPatch(m, { is_default: true })}><Star class="size-3.5" />设为默认</button>
            {/if}
            <button class="btn btn-ghost btn-sm" onclick={() => quickPatch(m, { enabled: !m.enabled })}>{m.enabled ? '停用' : '启用'}</button>
            <button class="btn btn-ghost btn-sm btn-icon ml-auto text-bad-ink" title="删除" aria-label="删除" onclick={() => remove(m)}>
              <Trash2 class="size-4" />
            </button>
          {/snippet}
        </EntityCard>
      {/each}
    </div>
  {:else if !loading}
    <EmptyState
      icon={Bot}
      title="还没有配置翻译模型"
      text="添加一个 OpenAI 兼容的接口（比如中转站的地址和 Key），用户就能开始翻译了。"
      action={{ label: '添加模型', onclick: () => (editing = 'new') }}
    />
  {/if}
</div>

{#if editing}
  <TranslationModelModal
    model={editing === 'new' ? null : editing}
    suggestDefault={!models.length}
    sortOrder={models.length}
    onclose={() => (editing = null)}
    onsaved={saved}
  />
{/if}
