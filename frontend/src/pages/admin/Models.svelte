<script lang="ts">
  import { onMount } from 'svelte';
  import Modal from '../../components/Modal.svelte';
  import Switch from '../../components/Switch.svelte';
  import { api } from '../../lib/api';
  import { confirm } from '../../lib/confirm.svelte';
  import { Bot, ChevronDown, CircleCheck, CircleX, LoaderCircle, Pencil, Plus, RefreshCw, Star, Trash2, Info } from '../../lib/icons';
  import { toast } from '../../lib/toast.svelte';
  import type { ModelAdmin, ModelTest } from '../../lib/types';

  interface Draft {
    id: number | null;
    name: string;
    description: string;
    base_url: string;
    api_key: string;
    clear_api_key: boolean;
    api_key_masked: string;
    model: string;
    term_model: string;
    qps: number;
    pool_max_workers: string;
    send_temperature: boolean;
    json_mode: boolean;
    enabled: boolean;
    is_default: boolean;
    sort_order: number;
  }

  let models = $state<ModelAdmin[]>([]);
  let loading = $state(true);
  let draft = $state<Draft | null>(null);
  let saving = $state(false);
  let advanced = $state(false);
  let remoteModels = $state<string[]>([]);
  let probing = $state(false);
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

  function openCreate() {
    advanced = false;
    remoteModels = [];
    draft = {
      id: null,
      name: '',
      description: '',
      base_url: '',
      api_key: '',
      clear_api_key: false,
      api_key_masked: '',
      model: '',
      term_model: '',
      qps: 4,
      pool_max_workers: '',
      send_temperature: true,
      json_mode: false,
      enabled: true,
      is_default: models.length === 0,
      sort_order: models.length,
    };
  }

  function openEdit(m: ModelAdmin) {
    advanced = false;
    remoteModels = [];
    draft = {
      id: m.id,
      name: m.name,
      description: m.description,
      base_url: m.base_url,
      api_key: '',
      clear_api_key: false,
      api_key_masked: m.api_key_masked,
      model: m.model,
      term_model: m.term_model ?? '',
      qps: m.qps,
      pool_max_workers: m.pool_max_workers ? String(m.pool_max_workers) : '',
      send_temperature: m.send_temperature,
      json_mode: m.json_mode,
      enabled: m.enabled,
      is_default: m.is_default,
      sort_order: m.sort_order,
    };
  }

  function payload(d: Draft) {
    return {
      name: d.name.trim(),
      description: d.description.trim(),
      base_url: d.base_url.trim(),
      model: d.model.trim(),
      term_model: d.term_model.trim() || null,
      qps: Number(d.qps) || 4,
      pool_max_workers: d.pool_max_workers.trim() ? Number(d.pool_max_workers) : null,
      send_temperature: d.send_temperature,
      json_mode: d.json_mode,
      enabled: d.enabled,
      is_default: d.is_default,
      sort_order: Number(d.sort_order) || 0,
    };
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (!draft) return;
    saving = true;
    try {
      if (draft.id === null) {
        await api.admin.createModel({ ...payload(draft), api_key: draft.api_key.trim() });
        toast.success('模型已添加，可以点“测试连接”确认一下');
      } else {
        await api.admin.patchModel(draft.id, {
          ...payload(draft),
          ...(draft.api_key.trim() ? { api_key: draft.api_key.trim() } : {}),
          clear_api_key: draft.clear_api_key,
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

  async function probe() {
    if (!draft) return;
    probing = true;
    try {
      const { models: list } = await api.admin.probeModels({
        base_url: draft.base_url.trim(),
        api_key: draft.api_key.trim(),
        model_id: draft.id,
      });
      remoteModels = list;
      toast.success(list.length ? `获取到 ${list.length} 个模型，可在“模型名”中选择` : '接口没有返回模型列表');
    } catch (e) {
      toast.error(e);
    } finally {
      probing = false;
    }
  }

  async function test(m: ModelAdmin) {
    tests[m.id] = 'running';
    try {
      tests[m.id] = await api.admin.testModel(m.id);
    } catch (e) {
      tests[m.id] = { ok: false, error: e instanceof Error ? e.message : String(e), latency_ms: null, status: null, reply: null };
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
    <button class="btn btn-primary btn-sm ml-auto" onclick={openCreate}><Plus class="size-4" />添加模型</button>
  </div>

  {#if models.length}
    <div class="grid gap-4 lg:grid-cols-2">
      {#each models as m (m.id)}
        {@const result = tests[m.id]}
        <div class="card flex flex-col p-5 {m.enabled ? '' : 'opacity-65'}">
          <div class="flex items-start gap-3">
            <div class="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent"><Bot class="size-5" /></div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <h3 class="truncate text-[15px] font-semibold">{m.name}</h3>
                {#if m.is_default}
                  <span class="inline-flex items-center gap-1 rounded-full bg-warn-soft px-2 py-0.5 text-[11.5px] font-medium text-warn-ink">
                    <Star class="size-3" />默认
                  </span>
                {/if}
                {#if !m.enabled}<span class="rounded-full bg-surface-3 px-2 py-0.5 text-[11.5px] text-muted">已停用</span>{/if}
              </div>
              {#if m.description}<p class="mt-0.5 text-[12.5px] text-muted">{m.description}</p>{/if}
            </div>
          </div>

          <dl class="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[12.5px]">
            <dt class="text-muted">接口地址</dt>
            <dd class="truncate font-mono text-[12px]" title={m.base_url}>{m.base_url || 'https://api.openai.com/v1'}</dd>
            <dt class="text-muted">模型</dt>
            <dd class="truncate font-mono text-[12px]">{m.model}{m.term_model ? ` · 术语 ${m.term_model}` : ''}</dd>
            <dt class="text-muted">API Key</dt>
            <dd class="font-mono text-[12px]">{m.api_key_set ? m.api_key_masked : '未设置'}</dd>
            <dt class="text-muted">并发</dt>
            <dd>每秒 {m.qps} 个请求{m.pool_max_workers ? ` · ${m.pool_max_workers} 线程` : ''}</dd>
          </dl>

          {#if result && result !== 'running'}
            <div class="mt-3 flex gap-2 rounded-xl px-3 py-2 text-[12.5px] {result.ok ? 'bg-good-soft text-good-ink' : 'bg-bad-soft text-bad-ink'}">
              {#if result.ok}<CircleCheck class="mt-0.5 size-4 shrink-0" />{:else}<CircleX class="mt-0.5 size-4 shrink-0" />{/if}
              <p class="min-w-0 break-words">
                {#if result.ok}
                  连接正常 · {((result.latency_ms ?? 0) / 1000).toFixed(1)} 秒 · 回复：{result.reply || '（空）'}
                {:else}
                  {result.status ? `HTTP ${result.status} · ` : ''}{result.error}
                {/if}
              </p>
            </div>
          {/if}

          <div class="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3">
            <button class="btn btn-secondary btn-sm" disabled={result === 'running'} onclick={() => test(m)}>
              {#if result === 'running'}<LoaderCircle class="size-3.5 animate-spin" />{:else}<RefreshCw class="size-3.5" />{/if}
              测试连接
            </button>
            <button class="btn btn-ghost btn-sm" onclick={() => openEdit(m)}><Pencil class="size-3.5" />编辑</button>
            {#if !m.is_default && m.enabled}
              <button class="btn btn-ghost btn-sm" onclick={() => quickPatch(m, { is_default: true })}><Star class="size-3.5" />设为默认</button>
            {/if}
            <button class="btn btn-ghost btn-sm" onclick={() => quickPatch(m, { enabled: !m.enabled })}>{m.enabled ? '停用' : '启用'}</button>
            <button class="btn btn-ghost btn-sm btn-icon ml-auto text-bad-ink" title="删除" aria-label="删除" onclick={() => remove(m)}>
              <Trash2 class="size-4" />
            </button>
          </div>
        </div>
      {/each}
    </div>
  {:else if !loading}
    <div class="card flex flex-col items-center px-6 py-14 text-center">
      <div class="grid size-14 place-items-center rounded-2xl bg-accent-soft text-accent"><Bot class="size-6" /></div>
      <p class="mt-4 font-medium">还没有配置翻译模型</p>
      <p class="mt-1 max-w-sm text-[13px] text-muted">添加一个 OpenAI 兼容的接口（比如中转站的地址和 Key），用户就能开始翻译了。</p>
      <button class="btn btn-primary mt-5" onclick={openCreate}><Plus class="size-4" />添加模型</button>
    </div>
  {/if}
</div>

<Modal open={!!draft} title={draft?.id === null ? '添加模型' : `编辑「${draft?.name}」`} size="lg" onclose={() => (draft = null)}>
  {#if draft}
    <form id="model-form" class="space-y-4" onsubmit={save}>
      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label class="label" for="m-name">显示名称</label>
          <input id="m-name" class="field" required maxlength={64} placeholder="例如：DeepSeek V3" bind:value={draft.name} />
        </div>
        <div>
          <label class="label" for="m-desc">说明 <span class="font-normal text-muted">（用户可见）</span></label>
          <input id="m-desc" class="field" maxlength={255} placeholder="例如：速度快，适合日常论文" bind:value={draft.description} />
        </div>
      </div>
      <div>
        <label class="label" for="m-url">接口地址（Base URL）</label>
        <input id="m-url" class="field font-mono" placeholder="https://api.example.com/v1" bind:value={draft.base_url} />
        <p class="hint">一般以 /v1 结尾；留空则使用 OpenAI 官方地址。</p>
      </div>
      <div>
        <label class="label" for="m-key">API Key</label>
        <input
          id="m-key"
          class="field font-mono"
          type="password"
          autocomplete="off"
          placeholder={draft.id !== null && draft.api_key_masked ? `已保存 ${draft.api_key_masked}，留空保持不变` : 'sk-...'}
          bind:value={draft.api_key}
        />
      </div>
      <div>
        <label class="label" for="m-model">模型名</label>
        <div class="flex gap-2">
          <input id="m-model" class="field font-mono" required list="remote-models" placeholder="例如：deepseek-chat" bind:value={draft.model} />
          <button type="button" class="btn btn-secondary shrink-0" disabled={probing} onclick={probe}>
            {#if probing}<LoaderCircle class="size-4 animate-spin" />{/if}获取模型列表
          </button>
        </div>
        <datalist id="remote-models">
          {#each remoteModels as name (name)}<option value={name}></option>{/each}
        </datalist>
      </div>
      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label class="label" for="m-qps">每秒请求数（QPS）</label>
          <input id="m-qps" class="field" type="number" min="1" max="100" bind:value={draft.qps} />
          <p class="hint">按中转站的限速设置，太高会被限流</p>
        </div>
        <div class="divide-y divide-line">
          <Switch bind:checked={draft.enabled} label="启用" />
          <Switch bind:checked={draft.is_default} label="设为默认模型" />
        </div>
      </div>

      <button type="button" class="flex items-center gap-1.5 text-[13px] font-medium text-ink-2 hover:text-ink" onclick={() => (advanced = !advanced)}>
        <ChevronDown class="size-4 transition-transform {advanced ? 'rotate-180' : ''}" />高级参数
      </button>
      {#if advanced}
        <div class="animate-pop space-y-4 rounded-xl border border-line p-4">
          <div class="grid gap-4 sm:grid-cols-2">
            <div>
              <label class="label" for="m-term">术语提取模型 <span class="font-normal text-muted">（可选）</span></label>
              <input id="m-term" class="field font-mono" list="remote-models" placeholder="默认与翻译模型相同" bind:value={draft.term_model} />
            </div>
            <div>
              <label class="label" for="m-workers">工作线程数 <span class="font-normal text-muted">（可选）</span></label>
              <input id="m-workers" class="field" inputmode="numeric" placeholder="默认等于 QPS" bind:value={draft.pool_max_workers} />
            </div>
            <div>
              <label class="label" for="m-order">排序</label>
              <input id="m-order" class="field" type="number" bind:value={draft.sort_order} />
            </div>
          </div>
          <div class="divide-y divide-line">
            <Switch bind:checked={draft.send_temperature} label="发送 temperature=0" description="个别模型（如部分推理模型）不接受 temperature 参数时关闭" />
            <Switch bind:checked={draft.json_mode} label="允许 JSON 模式" description="接口支持 response_format=json_object 时可开启" />
            {#if draft.id !== null && draft.api_key_masked}
              <Switch bind:checked={draft.clear_api_key} label="清除已保存的 API Key" />
            {/if}
          </div>
        </div>
      {/if}
    </form>
  {/if}
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={() => (draft = null)}>取消</button>
    <button class="btn btn-primary" form="model-form" disabled={saving}>
      {#if saving}<LoaderCircle class="size-4 animate-spin" />{/if}保存
    </button>
  {/snippet}
</Modal>
