<script lang="ts">
  import { tick } from 'svelte';
  import ApiKeyField from '../../components/admin/ApiKeyField.svelte';
  import ModelNameField from '../../components/admin/ModelNameField.svelte';
  import Modal from '../../components/Modal.svelte';
  import Switch from '../../components/Switch.svelte';
  import { errorText } from '../../lib/format';
  import { LoaderCircle } from '../../lib/icons';
  import { meetingApi } from '../../lib/meeting/api';
  import { EFFORT_LABELS, EFFORT_ORDER } from '../../lib/meeting/llm';
  import type { EffortLevel, LlmModelAdmin, LlmProbeIn } from '../../lib/meeting/types';
  import type { ModelAdmin } from '../../lib/types';

  interface Props {
    /** null 表示新建 */
    model: LlmModelAdmin | null;
    /** 翻译模型，用来复制接口地址和 Key */
    sources: ModelAdmin[];
    /** 新建时“同时创建方案”是否默认打开（还没有方案时打开） */
    suggestPreset: boolean;
    sortOrder: number;
    onclose: () => void;
    onsaved: (saved: LlmModelAdmin, message: string) => void;
  }

  let { model, sources, suggestPreset, sortOrder, onclose, onsaved }: Props = $props();

  interface Draft {
    name: string;
    description: string;
    base_url: string;
    api_key: string;
    clear_api_key: boolean;
    copy_from: number | null;
    model: string;
    effort_levels: EffortLevel[];
    /** 新建时还没动过档位：不发送，由后端按模型名套内置表 */
    efforts_auto: boolean;
    qps: number;
    json_mode: boolean;
    context_chars: number | null;
    enabled: boolean;
    sort_order: number;
    create_preset: boolean;
  }

  interface Note {
    tone: 'good' | 'bad' | 'info';
    text: string;
  }

  const NOTE_TONES = { good: 'text-good-ink', bad: 'text-bad-ink', info: 'text-muted' };

  function initial(): Draft {
    if (model) {
      return {
        name: model.name,
        description: model.description,
        base_url: model.base_url,
        api_key: '',
        clear_api_key: false,
        copy_from: null,
        model: model.model,
        effort_levels: [...model.effort_levels],
        efforts_auto: false,
        qps: model.qps,
        json_mode: model.json_mode,
        context_chars: model.context_chars,
        enabled: model.enabled,
        sort_order: model.sort_order,
        create_preset: false,
      };
    }
    return {
      name: '',
      description: '',
      base_url: '',
      api_key: '',
      clear_api_key: false,
      copy_from: null,
      model: '',
      effort_levels: [],
      efforts_auto: true,
      qps: 3,
      json_mode: false,
      context_chars: null,
      enabled: true,
      sort_order: sortOrder,
      create_preset: suggestPreset,
    };
  }

  let draft = $state<Draft>(initial());
  let saving = $state(false);
  let error = $state('');
  let errorBox = $state<HTMLElement>();
  let detecting = $state(false);
  let effortNote = $state<Note | null>(null);

  const source = $derived(sources.find((s) => s.id === draft.copy_from));
  // 内置表是按已保存的模型名算的；改了模型名就对不上了
  const builtinStale = $derived(model !== null && draft.model.trim() !== model.model);

  function chooseSource(id: number | null) {
    draft.copy_from = id;
    if (id === null) {
      // 不复制了：编辑时把清掉的地址填回来，否则保存会把地址改成空（即 OpenAI 官方地址）
      if (model && !draft.base_url) draft.base_url = model.base_url;
      return;
    }
    // 清空地址和 Key，保存时由服务器复制；之后自己再填的以填的为准
    draft.base_url = '';
    draft.api_key = '';
    draft.clear_api_key = false;
  }

  /** 拉取列表、检测档位用的连接：选了复制来源或要清掉 Key 时，不再用这个模型已保存的地址和 Key */
  function credentials(): LlmProbeIn {
    return {
      base_url: draft.base_url.trim(),
      api_key: draft.api_key.trim(),
      model: draft.model.trim(),
      model_id: model && draft.copy_from === null && !draft.clear_api_key ? model.id : null,
      copy_from_model_id: draft.copy_from,
    };
  }

  async function probe() {
    return (await meetingApi.admin.probeLlmModels(credentials())).models;
  }

  function toggleEffort(level: EffortLevel) {
    const on = new Set(draft.effort_levels);
    if (on.has(level)) on.delete(level);
    else on.add(level);
    draft.effort_levels = EFFORT_ORDER.filter((l) => on.has(l));
    draft.efforts_auto = false;
    effortNote = null;
  }

  async function detect() {
    if (!draft.model.trim()) {
      effortNote = { tone: 'bad', text: '请先填写模型名' };
      return;
    }
    detecting = true;
    effortNote = null;
    try {
      const result = await meetingApi.admin.detectEfforts(credentials());
      draft.effort_levels = EFFORT_ORDER.filter((l) => result.suggested.includes(l));
      draft.efforts_auto = false;
      effortNote = { tone: result.detected.length ? 'good' : 'info', text: result.message };
    } catch (e) {
      effortNote = { tone: 'bad', text: errorText(e) };
    } finally {
      detecting = false;
    }
  }

  function useBuiltin() {
    if (!model) {
      draft.effort_levels = [];
      draft.efforts_auto = true;
      effortNote = { tone: 'info', text: '保存时按模型名套用内置档位表' };
      return;
    }
    draft.effort_levels = [...model.builtin_effort_levels];
    draft.efforts_auto = false;
    effortNote = model.builtin_effort_levels.length
      ? { tone: 'info', text: `已按内置表填写：${model.builtin_effort_levels.map((l) => EFFORT_LABELS[l]).join('、')}` }
      : { tone: 'info', text: '内置表里没有这个模型，档位已清空（不发送思考参数）' };
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (saving) return;
    const d = draft;
    const key = d.api_key.trim();
    const common = {
      name: d.name.trim(),
      description: d.description.trim(),
      base_url: d.base_url.trim(),
      model: d.model.trim(),
      qps: Number(d.qps) || 3,
      json_mode: d.json_mode,
      // 输入框清空后是 null；0 也当成没填
      context_chars: d.context_chars ? Number(d.context_chars) : null,
      enabled: d.enabled,
      sort_order: Number(d.sort_order) || 0,
    };
    saving = true;
    error = '';
    try {
      if (model === null) {
        const saved = await meetingApi.admin.createLlmModel({
          ...common,
          api_key: key,
          copy_from_model_id: d.copy_from,
          ...(d.efforts_auto ? {} : { effort_levels: d.effort_levels }),
          create_preset: d.create_preset,
        });
        onsaved(saved, d.create_preset ? `已添加，并建好了方案「${saved.name}」` : '已添加，可以点“测试”确认一下');
      } else {
        const saved = await meetingApi.admin.patchLlmModel(model.id, {
          ...common,
          // 点了“清空”就不发新 Key（输入框已禁用并清空，这里再保险一次）
          ...(key && !d.clear_api_key ? { api_key: key } : {}),
          clear_api_key: d.clear_api_key,
          copy_from_model_id: d.copy_from,
          effort_levels: d.effort_levels,
        });
        onsaved(saved, '已保存');
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

<Modal open title={model ? `编辑「${model.name}」` : '添加会议模型'} size="lg" {onclose}>
  <form id="meeting-llm-form" class="space-y-4" onsubmit={save}>
    <div class="grid gap-4 sm:grid-cols-2">
      <div>
        <label class="label" for="ml-name">显示名称</label>
        <input id="ml-name" class="field" required maxlength={64} placeholder="例如：GPT-6 Astra" bind:value={draft.name} />
      </div>
      <div>
        <label class="label" for="ml-desc">说明 <span class="font-normal text-muted">（可选）</span></label>
        <input id="ml-desc" class="field" maxlength={255} placeholder="例如：旗舰模型，适合纪要和对话" bind:value={draft.description} />
      </div>
    </div>

    {#if sources.length}
      <div>
        <label class="label" for="ml-copy">从翻译模型复制地址和 Key</label>
        <select id="ml-copy" class="field" bind:value={() => draft.copy_from, chooseSource}>
          <option value={null}>不复制，自己填写</option>
          {#each sources as s (s.id)}<option value={s.id}>{s.name}（{s.model}）</option>{/each}
        </select>
        <p class="hint">Key 在服务器端复制，不经过浏览器；下面自己填了的以填的为准。</p>
      </div>
    {/if}

    <div>
      <label class="label" for="ml-url">接口地址（Base URL）</label>
      <input
        id="ml-url"
        class="field font-mono"
        autocomplete="off"
        spellcheck="false"
        placeholder={source ? source.base_url || 'https://api.openai.com/v1' : 'https://api.example.com/v1'}
        bind:value={draft.base_url}
      />
      <p class="hint">{source ? `留空则用「${source.name}」的地址` : '一般以 /v1 结尾；留空则使用 OpenAI 官方地址。'}</p>
    </div>
    <div>
      <label class="label" for="ml-key">API Key</label>
      <ApiKeyField
        id="ml-key"
        bind:value={draft.api_key}
        bind:clear={draft.clear_api_key}
        saved={!source && model?.api_key_set ? model.api_key_masked : ''}
        placeholder={source ? `留空则复制「${source.name}」的 Key` : 'sk-...'}
      />
      <p class="hint">加密保存在服务器，不会发送到浏览器。</p>
    </div>
    <div>
      <label class="label" for="ml-model">模型名</label>
      <ModelNameField
        id="ml-model"
        bind:value={draft.model}
        listId="meeting-llm-remote-models"
        placeholder="例如：gpt-6-astra"
        maxlength={128}
        {probe}
      />
    </div>

    <div>
      <div class="mb-1.5 flex flex-wrap items-center gap-2">
        <span class="text-[13px] font-medium text-ink-2" id="ml-efforts-label">思考档位</span>
        <div class="ml-auto flex gap-1.5">
          <button
            type="button"
            class="btn btn-secondary btn-sm"
            disabled={detecting}
            title="发一个不存在的档位，从接口的报错里读出可用档位"
            onclick={detect}
          >
            {#if detecting}<LoaderCircle class="size-3.5 animate-spin" />{/if}检测
          </button>
          <button
            type="button"
            class="btn btn-ghost btn-sm"
            disabled={builtinStale}
            title={builtinStale ? '改了模型名：先保存再按内置表，或者点“检测”' : '按模型名套用内置档位表'}
            onclick={useBuiltin}
          >
            按内置表
          </button>
        </div>
      </div>
      <div class="flex flex-wrap gap-1.5" role="group" aria-labelledby="ml-efforts-label">
        {#each EFFORT_ORDER as level (level)}
          {@const on = draft.effort_levels.includes(level)}
          <button
            type="button"
            aria-pressed={on}
            class="inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-[12.5px] font-medium transition-colors
              {on ? 'border-accent bg-accent-soft text-accent' : 'border-line-strong text-ink-2 hover:text-ink'}"
            onclick={() => toggleEffort(level)}
          >
            {EFFORT_LABELS[level]}<span class="font-mono text-[11px] opacity-70">{level}</span>
          </button>
        {/each}
      </div>
      {#if effortNote}
        <p class="hint {NOTE_TONES[effortNote.tone]}">{effortNote.text}</p>
      {:else if draft.efforts_auto}
        <p class="hint">还没选：保存时按模型名套用内置档位表，也可以点“检测”从接口读取。</p>
      {:else}
        <p class="hint">选中的档位会出现在方案的“思考强度”里；都不选表示这个模型不发送思考参数。</p>
      {/if}
    </div>

    <div class="grid gap-4 sm:grid-cols-3">
      <div>
        <label class="label" for="ml-qps">每秒请求数（QPS）</label>
        <input id="ml-qps" class="field" type="number" min="1" max="100" required bind:value={draft.qps} />
      </div>
      <div>
        <label class="label" for="ml-ctx">上下文字数 <span class="font-normal text-muted">（可选）</span></label>
        <input
          id="ml-ctx"
          class="field"
          type="number"
          min="10000"
          max="2000000"
          placeholder="用系统设置"
          bind:value={draft.context_chars}
        />
      </div>
      <div>
        <label class="label" for="ml-order">排序</label>
        <input id="ml-order" class="field" type="number" bind:value={draft.sort_order} />
      </div>
    </div>
    <p class="-mt-2 text-[12px] text-muted">上下文字数是一次能放进多少字逐字稿，留空用系统设置里的“大模型上下文预算”。</p>

    <div class="divide-y divide-line">
      <Switch bind:checked={draft.json_mode} label="JSON 模式" description="接口支持 response_format=json_object 时打开" />
      <Switch bind:checked={draft.enabled} label="启用" />
      {#if model === null}
        <Switch
          bind:checked={draft.create_preset}
          label="同时创建一个四个用途都用它的方案"
          description="参数都用模型默认值；还没有方案时它会成为默认方案"
        />
      {/if}
    </div>

    {#if error}
      <p bind:this={errorBox} class="rounded-lg bg-bad-soft px-3 py-2 text-[13px] text-bad-ink" role="alert">{error}</p>
    {/if}
  </form>
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={onclose}>取消</button>
    <button class="btn btn-primary" form="meeting-llm-form" disabled={saving}>
      {#if saving}<LoaderCircle class="size-4 animate-spin" />{/if}保存
    </button>
  {/snippet}
</Modal>
