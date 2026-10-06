<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../../lib/api';
  import { confirm } from '../../lib/confirm.svelte';
  import {
    BrainCircuit,
    CircleCheck,
    CircleX,
    FlaskConical,
    Info,
    LoaderCircle,
    Pencil,
    Plus,
    SlidersHorizontal,
    Star,
    Trash2,
    TriangleAlert,
  } from '../../lib/icons';
  import { meetingApi } from '../../lib/meeting/api';
  import { STEP_LABELS } from '../../lib/meeting/format';
  import { EFFORT_LABELS, STEPS, effortShort, failedTest, stepFacts, testSummary } from '../../lib/meeting/llm';
  import type { LlmModelAdmin, LlmTestResult, PresetAdmin } from '../../lib/meeting/types';
  import { toast } from '../../lib/toast.svelte';
  import type { ModelAdmin } from '../../lib/types';
  import LlmModelModal from './LlmModelModal.svelte';
  import PresetModal from './PresetModal.svelte';

  let models = $state<LlmModelAdmin[]>([]);
  let presets = $state<PresetAdmin[]>([]);
  /** 翻译模型，只用来“从翻译模型复制地址和 Key” */
  let sources = $state<ModelAdmin[]>([]);
  let loading = $state(true);
  /** 'new' 表示新建 */
  let editingModel = $state<LlmModelAdmin | 'new' | null>(null);
  let editingPreset = $state<PresetAdmin | 'new' | null>(null);
  let tests = $state<Record<number, LlmTestResult | 'running'>>({});

  const modelMap = $derived(new Map(models.map((m) => [m.id, m])));

  // 模型和方案互相引用（用到它的方案、方案里的问题），任何一边改了都两边一起刷新
  async function load() {
    try {
      const [m, p] = await Promise.all([meetingApi.admin.llmModels(), meetingApi.admin.presets()]);
      models = m;
      presets = p;
    } catch (e) {
      toast.error(e);
    }
  }

  onMount(() => {
    void load().finally(() => (loading = false));
    // 拿不到就不显示复制的选项
    api.admin
      .models()
      .then((list) => (sources = list))
      .catch(() => {});
  });

  async function modelSaved(saved: LlmModelAdmin, message: string) {
    // 配置变了，之前的测试结果不再作数
    delete tests[saved.id];
    editingModel = null;
    toast.success(message);
    await load();
  }

  async function presetSaved(message: string) {
    editingPreset = null;
    toast.success(message);
    await load();
  }

  async function test(m: LlmModelAdmin) {
    tests[m.id] = 'running';
    try {
      tests[m.id] = await meetingApi.admin.testLlmModel(m.id, 'default');
    } catch (e) {
      tests[m.id] = failedTest(e);
    }
  }

  async function toggleModel(m: LlmModelAdmin) {
    if (m.enabled && m.used_by.length) {
      const ok = await confirm({
        title: `停用「${m.name}」？`,
        message: `方案“${m.used_by.join('、')}”用到这个模型。停用后，这些方案里用它的用途会无法运行，直到换成别的模型或重新启用。`,
        confirmText: '停用',
        danger: true,
      });
      if (!ok) return;
    }
    try {
      await meetingApi.admin.patchLlmModel(m.id, { enabled: !m.enabled });
      await load();
    } catch (e) {
      toast.error(e);
    }
  }

  async function removeModel(m: LlmModelAdmin) {
    const ok = await confirm({
      title: `删除会议模型「${m.name}」？`,
      message: '已经整理过的会议不受影响。还有方案在用它时不能删除，要先在方案里换成别的模型。',
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await meetingApi.admin.deleteLlmModel(m.id);
      delete tests[m.id];
      toast.success('已删除');
      await load();
    } catch (e) {
      toast.error(e);
    }
  }

  async function patchPreset(p: PresetAdmin, body: { is_default?: boolean; enabled?: boolean }) {
    try {
      await meetingApi.admin.patchPreset(p.id, body);
      await load();
    } catch (e) {
      toast.error(e);
    }
  }

  async function removePreset(p: PresetAdmin) {
    const ok = await confirm({
      title: `删除方案「${p.name}」？`,
      message:
        '用这个方案的会议之后按默认方案整理和对话。' + (p.is_default ? '它是默认方案，删除后会自动把排在最前面的启用方案设为默认。' : ''),
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await meetingApi.admin.deletePreset(p.id);
      toast.success('已删除');
      await load();
    } catch (e) {
      toast.error(e);
    }
  }
</script>

<div class="space-y-8">
  <p class="flex items-start gap-1.5 text-[13px] text-muted">
    <Info class="mt-0.5 size-4 shrink-0" />
    会议记录用的大模型与翻译模型分开配置。先添加模型，再把模型组合成“整理方案”；成员上传会议时选方案。
  </p>

  {#if loading}
    <div class="flex justify-center py-20"><LoaderCircle class="size-6 animate-spin text-muted" /></div>
  {:else}
    <section class="space-y-4">
      <div class="flex flex-wrap items-center gap-3">
        <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><BrainCircuit class="size-4 text-accent" />模型</h2>
        <button class="btn btn-primary btn-sm ml-auto" onclick={() => (editingModel = 'new')}><Plus class="size-4" />添加模型</button>
      </div>

      {#if models.length}
        <div class="grid gap-4 lg:grid-cols-2">
          {#each models as m (m.id)}
            {@const result = tests[m.id]}
            <div class="card flex flex-col p-5 {m.enabled ? '' : 'opacity-65'}">
              <div class="flex items-start gap-3">
                <div class="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent"><BrainCircuit class="size-5" /></div>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <h3 class="truncate text-[15px] font-semibold">{m.name}</h3>
                    {#if !m.enabled}<span class="rounded-full bg-surface-3 px-2 py-0.5 text-[11.5px] text-muted">已停用</span>{/if}
                  </div>
                  {#if m.description}<p class="mt-0.5 text-[12.5px] text-muted">{m.description}</p>{/if}
                </div>
              </div>

              <dl class="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[12.5px]">
                <dt class="text-muted">接口地址</dt>
                <dd class="truncate font-mono text-[12px] text-muted" title={m.base_url}>{m.base_url || 'https://api.openai.com/v1'}</dd>
                <dt class="text-muted">模型</dt>
                <dd class="truncate font-mono text-[12px]" title={m.model}>{m.model}</dd>
                <dt class="text-muted">API Key</dt>
                <dd class="truncate font-mono text-[12px] {m.api_key_set ? '' : 'text-muted'}">{m.api_key_set ? m.api_key_masked : '未设置'}</dd>
                <dt class="text-muted">思考档位</dt>
                <dd class="flex flex-wrap gap-1">
                  {#each m.effort_levels as level (level)}
                    <span class="rounded-full bg-surface-2 px-2 py-0.5 text-[11.5px] text-ink-2" title={level}>{EFFORT_LABELS[level]}</span>
                  {:else}
                    <span class="text-muted">无思考档位</span>
                  {/each}
                </dd>
                <dt class="text-muted">并发</dt>
                <dd>
                  每秒 {m.qps} 个请求{m.json_mode ? ' · JSON 模式' : ''}{m.context_chars
                    ? ` · 上下文 ${m.context_chars.toLocaleString()} 字`
                    : ''}
                </dd>
                <dt class="text-muted">方案</dt>
                <dd class="truncate {m.used_by.length ? '' : 'text-muted'}" title={m.used_by.join('、')}>
                  {m.used_by.length ? m.used_by.join('、') : '还没有方案用它'}
                </dd>
              </dl>

              {#if result && result !== 'running'}
                <div class="mt-3 flex gap-2 rounded-xl px-3 py-2 text-[12.5px] {result.ok ? 'bg-good-soft text-good-ink' : 'bg-bad-soft text-bad-ink'}">
                  {#if result.ok}<CircleCheck class="mt-0.5 size-4 shrink-0" />{:else}<CircleX class="mt-0.5 size-4 shrink-0" />{/if}
                  <p class="min-w-0 break-words">{testSummary(result)}</p>
                </div>
              {/if}

              <div class="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3">
                <button
                  class="btn btn-secondary btn-sm"
                  disabled={result === 'running'}
                  title="不带思考参数发一句话，看能不能连通"
                  onclick={() => test(m)}
                >
                  {#if result === 'running'}<LoaderCircle class="size-3.5 animate-spin" />{:else}<FlaskConical class="size-3.5" />{/if}
                  测试
                </button>
                <button class="btn btn-ghost btn-sm" onclick={() => (editingModel = m)}><Pencil class="size-3.5" />编辑</button>
                <button class="btn btn-ghost btn-sm" onclick={() => toggleModel(m)}>{m.enabled ? '停用' : '启用'}</button>
                <button class="btn btn-ghost btn-sm btn-icon ml-auto text-bad-ink" title="删除" aria-label="删除" onclick={() => removeModel(m)}>
                  <Trash2 class="size-4" />
                </button>
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <div class="card flex flex-col items-center px-6 py-14 text-center">
          <div class="grid size-14 place-items-center rounded-2xl bg-accent-soft text-accent"><BrainCircuit class="size-6" /></div>
          <p class="mt-4 font-medium">还没有会议用的大模型</p>
          <p class="mt-1 max-w-sm text-[13px] text-muted">
            添加一个 OpenAI 兼容的接口，可以直接复制翻译模型的地址和 Key。会议记录要靠它识别说话人、整理逐字稿、写纪要。
          </p>
          <button class="btn btn-primary mt-5" onclick={() => (editingModel = 'new')}><Plus class="size-4" />添加模型</button>
        </div>
      {/if}
    </section>

    <section class="space-y-4">
      <div class="flex flex-wrap items-center gap-3">
        <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><SlidersHorizontal class="size-4 text-accent" />整理方案</h2>
        {#if !models.length}<span class="text-[12.5px] text-muted">先添加模型</span>{/if}
        <button
          class="btn btn-primary btn-sm ml-auto"
          disabled={!models.length}
          title={models.length ? undefined : '先添加模型'}
          onclick={() => (editingPreset = 'new')}
        >
          <Plus class="size-4" />添加方案
        </button>
      </div>

      {#if presets.length}
        <div class="grid gap-4 lg:grid-cols-2">
          {#each presets as p (p.id)}
            <div class="card flex flex-col p-5 {p.enabled ? '' : 'opacity-65'}">
              <div class="flex items-start gap-3">
                <div class="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent"><SlidersHorizontal class="size-5" /></div>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <h3 class="truncate text-[15px] font-semibold">{p.name}</h3>
                    {#if p.is_default}
                      <span class="inline-flex items-center gap-1 rounded-full bg-warn-soft px-2 py-0.5 text-[11.5px] font-medium text-warn-ink">
                        <Star class="size-3" />默认
                      </span>
                    {/if}
                    {#if !p.enabled}<span class="rounded-full bg-surface-3 px-2 py-0.5 text-[11.5px] text-muted">已停用</span>{/if}
                  </div>
                  {#if p.description}<p class="mt-0.5 text-[12.5px] text-muted">{p.description}</p>{/if}
                </div>
              </div>

              <dl class="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-[12.5px]">
                {#each STEPS as step (step)}
                  {@const sc = p.steps[step]}
                  {@const m = sc.model_id !== null ? modelMap.get(sc.model_id) : undefined}
                  <dt class="text-muted">{STEP_LABELS[step]}</dt>
                  <dd class="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
                    {#if m}
                      <span class="min-w-0 truncate font-medium {m.enabled ? '' : 'text-muted line-through'}" title={m.model}>{m.name}</span>
                    {:else}
                      <span class="text-bad-ink">没有模型</span>
                    {/if}
                    <span class="rounded-full bg-surface-2 px-2 py-0.5 text-[11.5px] text-ink-2">思考 {effortShort(sc.effort)}</span>
                    {#each stepFacts(sc, step) as fact (fact)}
                      <span class="rounded-full bg-surface-2 px-2 py-0.5 text-[11.5px] text-ink-2">{fact}</span>
                    {/each}
                  </dd>
                {/each}
              </dl>

              {#if p.problems.length}
                <div class="mt-3 flex gap-2 rounded-xl bg-warn-soft px-3 py-2 text-[12.5px] text-warn-ink">
                  <TriangleAlert class="mt-0.5 size-4 shrink-0" />
                  <ul class="min-w-0 space-y-0.5 break-words">
                    {#each p.problems as problem, i (i)}<li>{problem}</li>{/each}
                  </ul>
                </div>
              {/if}

              <div class="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3">
                <button class="btn btn-secondary btn-sm" onclick={() => (editingPreset = p)}><Pencil class="size-3.5" />编辑</button>
                {#if !p.is_default && p.enabled}
                  <button class="btn btn-ghost btn-sm" onclick={() => patchPreset(p, { is_default: true })}><Star class="size-3.5" />设为默认</button>
                {/if}
                <button class="btn btn-ghost btn-sm" onclick={() => patchPreset(p, { enabled: !p.enabled })}>{p.enabled ? '停用' : '启用'}</button>
                <button class="btn btn-ghost btn-sm btn-icon ml-auto text-bad-ink" title="删除" aria-label="删除" onclick={() => removePreset(p)}>
                  <Trash2 class="size-4" />
                </button>
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <div class="card flex flex-col items-center px-6 py-14 text-center">
          <div class="grid size-14 place-items-center rounded-2xl bg-accent-soft text-accent"><SlidersHorizontal class="size-6" /></div>
          <p class="mt-4 font-medium">还没有整理方案</p>
          <p class="mt-1 max-w-sm text-[13px] text-muted">
            {models.length
              ? '方案决定识别说话人、整理逐字稿、生成纪要和对话问答各用哪个模型、什么参数。没有方案时会议没法整理。'
              : '先在上面添加模型，再把模型组合成方案。添加模型时也可以顺带建一个方案。'}
          </p>
          {#if models.length}
            <button class="btn btn-primary mt-5" onclick={() => (editingPreset = 'new')}><Plus class="size-4" />添加方案</button>
          {/if}
        </div>
      {/if}
    </section>
  {/if}
</div>

{#if editingModel}
  <LlmModelModal
    model={editingModel === 'new' ? null : editingModel}
    {sources}
    suggestPreset={!presets.length}
    sortOrder={models.length}
    onclose={() => (editingModel = null)}
    onsaved={modelSaved}
  />
{/if}

{#if editingPreset}
  <PresetModal
    preset={editingPreset === 'new' ? null : editingPreset}
    {models}
    suggestDefault={!presets.length}
    sortOrder={presets.length}
    onclose={() => (editingPreset = null)}
    onsaved={presetSaved}
  />
{/if}
