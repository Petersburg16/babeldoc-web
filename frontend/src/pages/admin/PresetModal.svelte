<script module lang="ts">
  import type { CustomParam, EffortChoice, LlmStep, StepConfig } from '../../lib/meeting/types';
  import { EFFORT_LABELS } from './LlmModelModal.svelte';

  export const STEPS: LlmStep[] = ['speakers', 'polish', 'minutes', 'chat'];
  export const STEP_LABELS: Record<LlmStep, string> = {
    speakers: '猜说话人',
    polish: '整理逐字稿',
    minutes: '生成纪要',
    chat: '对话问答',
  };
  // 与后端 DEFAULT_TIMEOUTS 一致
  const DEFAULT_TIMEOUTS: Record<LlmStep, number> = { speakers: 300, polish: 300, minutes: 900, chat: 600 };

  export function effortShort(choice: EffortChoice) {
    return choice === 'default' ? '默认' : EFFORT_LABELS[choice];
  }

  /** 打开了的参数开关、改过的超时和自定义参数，用于卡片和折叠时的摘要 */
  export function stepFacts(sc: StepConfig, step: LlmStep): string[] {
    const facts: string[] = [];
    if (sc.temperature.on) facts.push(`温度 ${sc.temperature.value}`);
    if (sc.top_p.on) facts.push(`Top-P ${sc.top_p.value}`);
    if (sc.max_tokens.on) facts.push(`最大输出 ${sc.max_tokens.value}`);
    if (sc.timeout_s !== DEFAULT_TIMEOUTS[step]) facts.push(`超时 ${sc.timeout_s} 秒`);
    if (sc.params.length) facts.push(`自定义 ${sc.params.map((p) => p.name).join('、')}`);
    return facts;
  }

  const RESERVED_PARAMS = ['model', 'messages', 'stream', 'stream_options'];
  const PARAM_NAME = /^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/;
  const NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;

  /** 和后端 CustomParam 的校验一致，空字符串表示没问题 */
  function paramError(sc: StepConfig, index: number): string {
    const p = sc.params[index];
    const name = p.name.trim();
    if (!name) return '请填写参数名';
    if (RESERVED_PARAMS.includes(name)) return `“${name}”由本站自己填写，不能作为自定义参数`;
    if (!PARAM_NAME.test(name)) return '参数名只能用字母、数字、下划线、点和连字符，且以字母或下划线开头';
    if (sc.params.findIndex((q) => q.name.trim() === name) !== index) return '参数名重复了';
    const toggled: Record<string, boolean> = {
      temperature: sc.temperature.on,
      top_p: sc.top_p.on,
      max_tokens: sc.max_tokens.on,
      max_completion_tokens: sc.max_tokens.on,
    };
    if (toggled[name]) return '和上面打开的开关重复了，请只保留一处';
    const raw = p.value.trim();
    if (p.type === 'number' && !NUMBER.test(raw)) return '不是数字';
    if (p.type === 'boolean' && raw !== 'true' && raw !== 'false') return '只能是 true 或 false';
    if (p.type === 'json') {
      try {
        JSON.parse(raw);
      } catch {
        return 'JSON 格式错误';
      }
    }
    return '';
  }

  const inRange = (v: unknown, min: number, max: number, integer = false) =>
    typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max && (!integer || Number.isInteger(v));

  const PARAM_TYPES: { value: CustomParam['type']; label: string }[] = [
    { value: 'string', label: '文本' },
    { value: 'number', label: '数字' },
    { value: 'boolean', label: '布尔' },
    { value: 'json', label: 'JSON' },
  ];
</script>

<script lang="ts">
  import { tick } from 'svelte';
  import Modal from '../../components/Modal.svelte';
  import Switch from '../../components/Switch.svelte';
  import { confirm } from '../../lib/confirm.svelte';
  import { errorText } from '../../lib/format';
  import { ChevronDown, CircleCheck, CircleX, Copy, FlaskConical, LoaderCircle, Plus, Trash2, TriangleAlert } from '../../lib/icons';
  import { meetingApi } from '../../lib/meeting/api';
  import type { LlmModelAdmin, LlmTestResult, PresetAdmin, PresetSteps } from '../../lib/meeting/types';
  import { failedTest, testSummary } from './LlmModelModal.svelte';

  interface Props {
    /** null 表示新建 */
    preset: PresetAdmin | null;
    models: LlmModelAdmin[];
    /** 新建时默认勾上“设为默认”（还没有方案时） */
    suggestDefault: boolean;
    sortOrder: number;
    onclose: () => void;
    onsaved: (message: string) => void;
  }

  let { preset, models, suggestDefault, sortOrder, onclose, onsaved }: Props = $props();

  const STEP_TITLES: Record<LlmStep, string> = { ...STEP_LABELS, minutes: '生成纪要（含分段提要）' };
  const STEP_HINTS: Record<LlmStep, string> = {
    speakers: '根据逐字稿猜每位说话人是谁，每场会议调用一次',
    polish: '逐块整理逐字稿，调用次数多，适合快的模型',
    minutes: '按模板写纪要；逐字稿较长时先逐段写提要',
    chat: '成员就会议内容提问',
  };

  interface Draft {
    name: string;
    description: string;
    is_default: boolean;
    enabled: boolean;
    sort_order: number;
    steps: PresetSteps;
  }

  function newStep(step: LlmStep, modelId: number | null): StepConfig {
    return {
      model_id: modelId,
      effort: 'default',
      temperature: { on: false, value: 1 },
      top_p: { on: false, value: 1 },
      max_tokens: { on: false, value: 4096 },
      timeout_s: DEFAULT_TIMEOUTS[step],
      params: [],
    };
  }

  function initial(): Draft {
    if (preset) {
      const steps = $state.snapshot(preset.steps) as PresetSteps;
      // 模型被删掉了就显示成“请选择”，免得下拉框里出现一个不存在的选项
      for (const step of STEPS) {
        if (!models.some((m) => m.id === steps[step].model_id)) steps[step].model_id = null;
      }
      return {
        name: preset.name,
        description: preset.description,
        is_default: preset.is_default,
        enabled: preset.enabled,
        sort_order: preset.sort_order,
        steps,
      };
    }
    const first = models.find((m) => m.enabled)?.id ?? null;
    return {
      name: '',
      description: '',
      is_default: suggestDefault,
      enabled: true,
      sort_order: sortOrder,
      steps: {
        speakers: newStep('speakers', first),
        polish: newStep('polish', first),
        minutes: newStep('minutes', first),
        chat: newStep('chat', first),
      },
    };
  }

  /** 打开时就展开改过高级参数的用途 */
  function initialAdvanced(steps: PresetSteps): Record<LlmStep, boolean> {
    const open = (step: LlmStep) => stepFacts(steps[step], step).length > 0;
    return { speakers: open('speakers'), polish: open('polish'), minutes: open('minutes'), chat: open('chat') };
  }

  let draft = $state<Draft>(initial());
  let advanced = $state<Record<LlmStep, boolean>>(initialAdvanced(draft.steps));
  let tests = $state<Partial<Record<LlmStep, LlmTestResult | 'running'>>>({});
  let saving = $state(false);
  let error = $state('');
  let errorBox = $state<HTMLElement>();

  const modelMap = $derived(new Map(models.map((m) => [m.id, m])));
  // 启用的排在前面
  const modelOptions = $derived([...models.filter((m) => m.enabled), ...models.filter((m) => !m.enabled)]);

  function ladderOf(sc: StepConfig) {
    return sc.model_id !== null ? (modelMap.get(sc.model_id)?.effort_levels ?? []) : [];
  }

  function setModel(step: LlmStep, id: number | null) {
    const sc = draft.steps[step];
    sc.model_id = id;
    // 新模型的档位表里没有原来选的强度，就退回“默认”
    if (sc.effort !== 'default' && !ladderOf(sc).includes(sc.effort)) sc.effort = 'default';
  }

  function setParamType(sc: StepConfig, index: number, type: CustomParam['type']) {
    const p = sc.params[index];
    p.type = type;
    if (type === 'boolean' && p.value.trim() !== 'true' && p.value.trim() !== 'false') p.value = 'true';
  }

  /** 保存前检查一个用途，返回给人看的错误；null 表示没问题 */
  function stepError(step: LlmStep): { text: string; advanced: boolean } | null {
    const sc = draft.steps[step];
    const label = STEP_LABELS[step];
    const model = sc.model_id !== null ? modelMap.get(sc.model_id) : undefined;
    if (!model) return { text: `请给“${label}”选一个模型`, advanced: false };
    if (sc.effort !== 'default' && !model.effort_levels.includes(sc.effort)) {
      return { text: `“${label}”的思考强度不在模型“${model.name}”的档位里，请重新选择`, advanced: false };
    }
    if (sc.temperature.on && !inRange(sc.temperature.value, 0, 2)) {
      return { text: `“${label}”的模型温度要在 0 到 2 之间`, advanced: true };
    }
    if (sc.top_p.on && !inRange(sc.top_p.value, 0, 1)) return { text: `“${label}”的 Top-P 要在 0 到 1 之间`, advanced: true };
    if (sc.max_tokens.on && !inRange(sc.max_tokens.value, 1, 1_000_000, true)) {
      return { text: `“${label}”的最大输出要是 1 到 1000000 之间的整数`, advanced: true };
    }
    if (!inRange(sc.timeout_s, 30, 7200, true)) return { text: `“${label}”的超时要是 30 到 7200 之间的整数（秒）`, advanced: true };
    for (let i = 0; i < sc.params.length; i++) {
      const problem = paramError(sc, i);
      if (problem) return { text: `“${label}”的自定义参数 ${sc.params[i].name.trim() || i + 1}：${problem}`, advanced: true };
    }
    return null;
  }

  /** 关着的开关不发送，但后端照样校验数值范围：不合法的换回默认值 */
  function cleanStep(sc: StepConfig): StepConfig {
    const keep = (on: boolean, value: number, ok: boolean, fallback: number) => ({ on, value: ok ? value : fallback });
    return {
      model_id: sc.model_id,
      effort: sc.effort,
      temperature: keep(sc.temperature.on, sc.temperature.value, inRange(sc.temperature.value, 0, 2), 1),
      top_p: keep(sc.top_p.on, sc.top_p.value, inRange(sc.top_p.value, 0, 1), 1),
      max_tokens: keep(sc.max_tokens.on, sc.max_tokens.value, inRange(sc.max_tokens.value, 1, 1_000_000, true), 4096),
      timeout_s: sc.timeout_s,
      params: sc.params.map((p) => ({ name: p.name.trim(), type: p.type, value: p.value })),
    };
  }

  async function showError(text: string) {
    error = text;
    await tick();
    errorBox?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (saving) return;
    for (const step of STEPS) {
      const problem = stepError(step);
      if (problem) {
        if (problem.advanced) advanced[step] = true;
        await showError(problem.text);
        return;
      }
    }
    const body = {
      name: draft.name.trim(),
      description: draft.description.trim(),
      is_default: draft.is_default,
      enabled: draft.enabled,
      sort_order: Number(draft.sort_order) || 0,
      steps: {
        speakers: cleanStep(draft.steps.speakers),
        polish: cleanStep(draft.steps.polish),
        minutes: cleanStep(draft.steps.minutes),
        chat: cleanStep(draft.steps.chat),
      },
    };
    saving = true;
    error = '';
    try {
      if (preset) {
        await meetingApi.admin.patchPreset(preset.id, body);
        onsaved('已保存');
      } else {
        await meetingApi.admin.createPreset(body);
        onsaved('方案已添加，可以在编辑里逐个用途测试');
      }
    } catch (e) {
      await showError(errorText(e));
    } finally {
      saving = false;
    }
  }

  async function copyToOthers(step: LlmStep) {
    const ok = await confirm({
      title: `把“${STEP_LABELS[step]}”的设置复制到其他用途？`,
      message: '模型、思考强度、温度、Top-P、最大输出和自定义参数会覆盖其他三个用途；各用途的超时保持不变。',
      confirmText: '复制',
    });
    if (!ok) return;
    const source = $state.snapshot(draft.steps[step]) as StepConfig;
    for (const other of STEPS) {
      if (other === step) continue;
      draft.steps[other] = { ...structuredClone(source), timeout_s: draft.steps[other].timeout_s };
      advanced[other] = advanced[step];
    }
  }

  /** 测试按已保存的设置进行：和保存的不一样时提醒一下 */
  function unsaved(step: LlmStep) {
    return !!preset && JSON.stringify($state.snapshot(draft.steps[step])) !== JSON.stringify(preset.steps[step]);
  }

  async function test(step: LlmStep) {
    if (!preset) return;
    tests[step] = 'running';
    try {
      tests[step] = await meetingApi.admin.testPreset(preset.id, step);
    } catch (e) {
      tests[step] = failedTest(e);
    }
  }
</script>

<Modal
  open
  title={preset ? `编辑方案「${preset.name}」` : '添加整理方案'}
  description="四个用途分别选模型和参数；成员上传会议时选方案。"
  size="lg"
  {onclose}
>
  <form id="meeting-preset-form" class="space-y-4" onsubmit={save}>
    <div class="grid gap-4 sm:grid-cols-2">
      <div>
        <label class="label" for="mp-name">方案名称</label>
        <input id="mp-name" class="field" required maxlength={64} placeholder="例如：精细" bind:value={draft.name} />
      </div>
      <div>
        <label class="label" for="mp-desc">说明 <span class="font-normal text-muted">（成员可见）</span></label>
        <input id="mp-desc" class="field" maxlength={255} placeholder="例如：纪要更细，生成稍慢" bind:value={draft.description} />
      </div>
    </div>
    <div class="grid gap-x-4 sm:grid-cols-[minmax(0,1fr)_8rem]">
      <div class="divide-y divide-line">
        <Switch bind:checked={draft.is_default} label="设为默认方案" description="上传时预先选中；老会议也按默认方案处理" />
        <Switch bind:checked={draft.enabled} label="启用" />
      </div>
      <div>
        <label class="label" for="mp-order">排序</label>
        <input id="mp-order" class="field" type="number" bind:value={draft.sort_order} />
      </div>
    </div>

    {#each STEPS as step (step)}
      {@const sc = draft.steps[step]}
      {@const ladder = ladderOf(sc)}
      {@const model = sc.model_id !== null ? modelMap.get(sc.model_id) : undefined}
      {@const facts = stepFacts(sc, step)}
      {@const result = tests[step]}
      <section class="rounded-xl border border-line p-4" aria-labelledby="mp-{step}-title">
        <div class="flex flex-wrap items-start gap-x-2 gap-y-1">
          <div class="min-w-0 flex-1">
            <h3 id="mp-{step}-title" class="text-[14px] font-semibold">{STEP_TITLES[step]}</h3>
            <p class="mt-0.5 text-[12px] text-muted">{STEP_HINTS[step]}</p>
          </div>
          <div class="-mr-1.5 flex shrink-0 gap-1">
            {#if preset}
              <button
                type="button"
                class="btn btn-ghost btn-sm"
                disabled={result === 'running'}
                title="按已保存的设置发一句话"
                onclick={() => test(step)}
              >
                {#if result === 'running'}<LoaderCircle class="size-3.5 animate-spin" />{:else}<FlaskConical class="size-3.5" />{/if}
                测试
              </button>
            {/if}
            <button type="button" class="btn btn-ghost btn-sm" title="复制到其他三个用途" onclick={() => copyToOthers(step)}>
              <Copy class="size-3.5" /><span class="hidden sm:inline">复制到其他用途</span>
            </button>
          </div>
        </div>

        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <label class="label" for="mp-{step}-model">模型</label>
            <select id="mp-{step}-model" class="field" required bind:value={() => sc.model_id, (id) => setModel(step, id)}>
              {#if sc.model_id === null}<option value={null} disabled>请选择模型</option>{/if}
              {#each modelOptions as m (m.id)}
                <option value={m.id}>{m.name}{m.enabled ? '' : '（已停用）'}</option>
              {/each}
            </select>
            {#if model && !model.enabled}<p class="hint text-warn-ink">这个模型已停用，用这个方案时这一步会失败</p>{/if}
          </div>
          <div>
            <label class="label" for="mp-{step}-effort">思考强度</label>
            <select id="mp-{step}-effort" class="field" disabled={!ladder.length && sc.effort === 'default'} bind:value={sc.effort}>
              <option value="default">默认（不发送）</option>
              {#each ladder as level (level)}<option value={level}>{EFFORT_LABELS[level]}（{level}）</option>{/each}
              {#if sc.effort !== 'default' && !ladder.includes(sc.effort)}
                <option value={sc.effort}>{EFFORT_LABELS[sc.effort]}（模型不支持，请重选）</option>
              {/if}
            </select>
            {#if model && !ladder.length}<p class="hint">这个模型没有思考档位</p>{/if}
          </div>
        </div>

        <button
          type="button"
          class="mt-3 flex max-w-full items-center gap-1.5 text-[13px] font-medium text-ink-2 hover:text-ink"
          aria-expanded={advanced[step]}
          onclick={() => (advanced[step] = !advanced[step])}
        >
          <ChevronDown class="size-4 shrink-0 transition-transform {advanced[step] ? 'rotate-180' : ''}" />高级参数
          {#if !advanced[step] && facts.length}
            <span class="truncate font-normal text-muted">· {facts.join(' · ')}</span>
          {/if}
        </button>

        {#if advanced[step]}
          <div class="animate-pop mt-3 space-y-3 rounded-xl bg-surface-2/60 p-3.5">
            <div>
              <Switch bind:checked={sc.temperature.on} label="模型温度" description={sc.temperature.on ? undefined : '模型默认'} />
              {#if sc.temperature.on}
                <div class="flex items-center gap-2.5 pb-1">
                  <span class="shrink-0 text-[12px] text-muted">精确</span>
                  <input
                    class="w-full min-w-0 accent-accent"
                    type="range"
                    min="0"
                    max="2"
                    step="0.1"
                    aria-label="模型温度"
                    bind:value={sc.temperature.value}
                  />
                  <span class="shrink-0 text-[12px] text-muted">创意</span>
                  <input
                    class="field h-8 w-20 shrink-0 text-[13px]"
                    type="number"
                    min="0"
                    max="2"
                    step="any"
                    aria-label="模型温度数值"
                    bind:value={sc.temperature.value}
                  />
                </div>
              {/if}
            </div>
            <div>
              <Switch bind:checked={sc.top_p.on} label="Top-P" description={sc.top_p.on ? undefined : '模型默认'} />
              {#if sc.top_p.on}
                <div class="flex items-center gap-2.5 pb-1">
                  <input
                    class="w-full min-w-0 accent-accent"
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    aria-label="Top-P"
                    bind:value={sc.top_p.value}
                  />
                  <input
                    class="field h-8 w-20 shrink-0 text-[13px]"
                    type="number"
                    min="0"
                    max="1"
                    step="any"
                    aria-label="Top-P 数值"
                    bind:value={sc.top_p.value}
                  />
                </div>
              {/if}
            </div>
            <div>
              <Switch bind:checked={sc.max_tokens.on} label="最大输出" description={sc.max_tokens.on ? undefined : '模型默认'} />
              {#if sc.max_tokens.on}
                <div class="flex items-center gap-2 pb-1">
                  <input
                    class="field h-8 w-32 text-[13px]"
                    type="number"
                    min="1"
                    max="1000000"
                    step="1"
                    aria-label="最大输出 token 数"
                    bind:value={sc.max_tokens.value}
                  />
                  <span class="text-[12px] text-muted">token</span>
                </div>
                {#if ladder.length}
                  <p class="flex gap-1.5 text-[12px] text-warn-ink">
                    <TriangleAlert class="mt-0.5 size-3.5 shrink-0" />思考消耗的 token 也算在最大输出里，设得太小可能只有思考、没有正文
                  </p>
                {/if}
              {/if}
            </div>
            <div>
              <label class="label" for="mp-{step}-timeout">超时（秒）</label>
              <input
                id="mp-{step}-timeout"
                class="field h-8 w-32 text-[13px]"
                type="number"
                min="30"
                max="7200"
                step="1"
                required
                bind:value={sc.timeout_s}
              />
              <p class="hint">多久没收到数据算超时；会思考的模型第一个字要等思考结束才到</p>
            </div>

            <div>
              <p class="label">自定义参数</p>
              {#if sc.params.length}
                <div class="space-y-2">
                  {#each sc.params as p, i (i)}
                    {@const problem = paramError(sc, i)}
                    <div>
                      <div class="grid grid-cols-[minmax(0,1fr)_5.5rem_auto] gap-2 sm:grid-cols-[minmax(0,9rem)_5.5rem_minmax(0,1fr)_auto]">
                        <input
                          class="field h-8 font-mono text-[13px]"
                          maxlength={64}
                          autocomplete="off"
                          spellcheck="false"
                          placeholder="参数名"
                          aria-label="参数名"
                          aria-invalid={!!problem && !!p.name.trim()}
                          bind:value={p.name}
                        />
                        <select
                          class="field h-8 text-[13px]"
                          aria-label="类型"
                          bind:value={() => p.type, (type) => setParamType(sc, i, type)}
                        >
                          {#each PARAM_TYPES as t (t.value)}<option value={t.value}>{t.label}</option>{/each}
                        </select>
                        <div class="order-last col-span-3 sm:order-none sm:col-span-1">
                          {#if p.type === 'boolean'}
                            <select class="field h-8 font-mono text-[13px]" aria-label="参数值" bind:value={p.value}>
                              <option value="true">true</option>
                              <option value="false">false</option>
                            </select>
                          {:else}
                            <input
                              class="field h-8 font-mono text-[13px]"
                              maxlength={4000}
                              autocomplete="off"
                              spellcheck="false"
                              placeholder={p.type === 'json' ? '例如：{"enable_thinking": false}' : '参数值'}
                              aria-label="参数值"
                              aria-invalid={!!problem && !!p.name.trim()}
                              bind:value={p.value}
                            />
                          {/if}
                        </div>
                        <button
                          type="button"
                          class="btn btn-ghost btn-sm btn-icon h-8 text-bad-ink"
                          title="删除"
                          aria-label="删除参数"
                          onclick={() => sc.params.splice(i, 1)}
                        >
                          <Trash2 class="size-3.5" />
                        </button>
                      </div>
                      {#if problem && p.name.trim()}<p class="hint text-bad-ink">{problem}</p>{/if}
                    </div>
                  {/each}
                </div>
              {/if}
              <button
                type="button"
                class="btn btn-secondary btn-sm {sc.params.length ? 'mt-2' : ''}"
                disabled={sc.params.length >= 20}
                onclick={() => sc.params.push({ name: '', type: 'string', value: '' })}
              >
                <Plus class="size-3.5" />添加参数
              </button>
              <p class="hint">最后合进请求体；model、messages、stream、stream_options 由本站填写，不能改。</p>
            </div>
          </div>
        {/if}

        {#if result && result !== 'running'}
          <div class="mt-3 rounded-xl px-3 py-2 text-[12.5px] {result.ok ? 'bg-good-soft text-good-ink' : 'bg-bad-soft text-bad-ink'}">
            <div class="flex gap-2">
              {#if result.ok}<CircleCheck class="mt-0.5 size-4 shrink-0" />{:else}<CircleX class="mt-0.5 size-4 shrink-0" />{/if}
              <p class="min-w-0 break-words">{testSummary(result)}</p>
            </div>
            {#if Object.keys(result.sent).length}
              <pre class="mt-2 max-h-40 overflow-auto rounded-lg bg-surface/70 px-2.5 py-2 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-ink-2">{JSON.stringify(result.sent, null, 2)}</pre>
            {/if}
          </div>
        {:else if result === 'running'}
          <p class="mt-3 flex items-center gap-1.5 text-[12.5px] text-muted">
            <LoaderCircle class="size-3.5 animate-spin" />正在测试，会思考的模型可能要等一两分钟…
          </p>
        {/if}
        {#if result && unsaved(step)}
          <p class="hint">改动还没保存，测试按已保存的设置进行。</p>
        {/if}
      </section>
    {/each}

    {#if error}
      <p bind:this={errorBox} class="rounded-lg bg-bad-soft px-3 py-2 text-[13px] text-bad-ink" role="alert">{error}</p>
    {/if}
  </form>
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={onclose}>取消</button>
    <button class="btn btn-primary" form="meeting-preset-form" disabled={saving}>
      {#if saving}<LoaderCircle class="size-4 animate-spin" />{/if}保存
    </button>
  {/snippet}
</Modal>
