<script lang="ts">
  import { tick } from 'svelte';
  import ApiKeyField from '../../components/admin/ApiKeyField.svelte';
  import ModelNameField from '../../components/admin/ModelNameField.svelte';
  import Modal from '../../components/Modal.svelte';
  import Switch from '../../components/Switch.svelte';
  import { api } from '../../lib/api';
  import { errorText } from '../../lib/format';
  import { ChevronDown, LoaderCircle } from '../../lib/icons';
  import type { ModelAdmin } from '../../lib/types';

  interface Props {
    /** null 表示新建 */
    model: ModelAdmin | null;
    /** 新建时“设为默认模型”是否默认打开（还没有模型时打开） */
    suggestDefault: boolean;
    sortOrder: number;
    onclose: () => void;
    onsaved: (message: string) => void;
  }

  let { model, suggestDefault, sortOrder, onclose, onsaved }: Props = $props();

  interface Draft {
    name: string;
    description: string;
    base_url: string;
    api_key: string;
    clear_api_key: boolean;
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

  function initial(): Draft {
    if (model) {
      return {
        name: model.name,
        description: model.description,
        base_url: model.base_url,
        api_key: '',
        clear_api_key: false,
        model: model.model,
        term_model: model.term_model ?? '',
        qps: model.qps,
        pool_max_workers: model.pool_max_workers ? String(model.pool_max_workers) : '',
        send_temperature: model.send_temperature,
        json_mode: model.json_mode,
        enabled: model.enabled,
        is_default: model.is_default,
        sort_order: model.sort_order,
      };
    }
    return {
      name: '',
      description: '',
      base_url: '',
      api_key: '',
      clear_api_key: false,
      model: '',
      term_model: '',
      qps: 4,
      pool_max_workers: '',
      send_temperature: true,
      json_mode: false,
      enabled: true,
      is_default: suggestDefault,
      sort_order: sortOrder,
    };
  }

  let draft = $state<Draft>(initial());
  let saving = $state(false);
  let advanced = $state(false);
  let error = $state('');
  let errorBox = $state<HTMLElement>();

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

  async function probe() {
    const { models } = await api.admin.probeModels({
      base_url: draft.base_url.trim(),
      api_key: draft.api_key.trim(),
      model_id: model?.id ?? null,
    });
    return models;
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (saving) return;
    const d = draft;
    const key = d.api_key.trim();
    saving = true;
    error = '';
    try {
      if (model === null) {
        await api.admin.createModel({ ...payload(d), api_key: key });
        onsaved('模型已添加，可以点“测试连接”确认一下');
      } else {
        await api.admin.patchModel(model.id, {
          ...payload(d),
          // 点了“清空”就不发新 Key：后端先看 clear_api_key，一起发的话新 Key 会被丢掉
          ...(key && !d.clear_api_key ? { api_key: key } : {}),
          clear_api_key: d.clear_api_key,
        });
        onsaved('已保存');
      }
    } catch (e) {
      error = errorText(e);
      // 报错在表单最底下，滚动到能看见的位置
      await tick();
      errorBox?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } finally {
      saving = false;
    }
  }
</script>

<Modal open title={model ? `编辑「${draft.name}」` : '添加模型'} size="lg" {onclose}>
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
      <ApiKeyField
        id="m-key"
        bind:value={draft.api_key}
        bind:clear={draft.clear_api_key}
        saved={model?.api_key_set ? model.api_key_masked : ''}
        placeholder="sk-..."
      />
    </div>
    <div>
      <label class="label" for="m-model">模型名</label>
      <ModelNameField id="m-model" bind:value={draft.model} listId="remote-models" placeholder="例如：deepseek-chat" {probe} />
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
        </div>
      </div>
    {/if}

    {#if error}
      <p bind:this={errorBox} class="rounded-lg bg-bad-soft px-3 py-2 text-[13px] text-bad-ink" role="alert">{error}</p>
    {/if}
  </form>
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={onclose}>取消</button>
    <button class="btn btn-primary" form="model-form" disabled={saving}>
      {#if saving}<LoaderCircle class="size-4 animate-spin" />{/if}保存
    </button>
  {/snippet}
</Modal>
