<script lang="ts">
  import { onMount } from 'svelte';
  import ApiKeyField from '../../components/admin/ApiKeyField.svelte';
  import EmptyState from '../../components/admin/EmptyState.svelte';
  import EntityCard from '../../components/admin/EntityCard.svelte';
  import ResultNote from '../../components/admin/ResultNote.svelte';
  import Modal from '../../components/Modal.svelte';
  import Switch from '../../components/Switch.svelte';
  import { api, ApiError } from '../../lib/api';
  import { confirm } from '../../lib/confirm.svelte';
  import { events } from '../../lib/events.svelte';
  import { errorText } from '../../lib/format';
  import {
    ArrowLeftRight,
    AudioWaveform,
    CircleCheck,
    CircleX,
    FlaskConical,
    Info,
    KeyRound,
    LoaderCircle,
    Pencil,
    Plus,
    Star,
    Trash2,
    TriangleAlert,
    X,
  } from '../../lib/icons';
  import { meetingApi } from '../../lib/meeting/api';
  import { spoken } from '../../lib/meeting/format';
  import type {
    FieldSpec,
    ProviderAdmin,
    ProviderCheck,
    ProviderKind,
    ProviderTestEvent,
    ProviderTestStep,
  } from '../../lib/meeting/types';
  import { toast } from '../../lib/toast.svelte';

  interface Draft {
    id: number | null;
    /** 空字符串表示还没选类型（新建的第一步） */
    kind: string;
    fields: FieldSpec[];
    name: string;
    description: string;
    config: Record<string, string>;
    secrets: Record<string, string>;
    /** 已保存的密钥（打码后），用作占位提示 */
    saved: Record<string, string>;
    clear: Record<string, boolean>;
    enabled: boolean;
    is_default: boolean;
    sort_order: number;
  }

  interface TestState {
    running: boolean;
    ok: boolean;
    steps: ProviderTestStep[];
    /** 超时没等到结束事件（事件流断过就会漏掉） */
    lost: boolean;
  }

  // 后端最多等服务商 240 秒，再留出生成测试音频和自查公网地址的时间
  const TEST_TIMEOUT_MS = 300_000;

  let providers = $state<ProviderAdmin[]>([]);
  let kinds = $state<ProviderKind[]>([]);
  let loading = $state(true);
  let publicUrl = $state<string | null>(null);
  let draft = $state<Draft | null>(null);
  let saving = $state(false);
  let checks = $state<Record<number, ProviderCheck | 'running'>>({});
  let tests = $state<Record<number, TestState>>({});
  const timers = new Map<number, ReturnType<typeof setTimeout>>();

  const kindMap = $derived(new Map(kinds.map((k) => [k.kind, k])));
  const draftKind = $derived(draft ? kindMap.get(draft.kind) : undefined);
  const needsPublicUrl = $derived(publicUrl === '' && providers.some((p) => p.kind !== 'mock'));

  async function load() {
    try {
      providers = await meetingApi.admin.providers();
    } catch (e) {
      toast.error(e);
    }
  }

  // 类型表到了再显示卡片，否则立刻点“编辑”会拿不到字段说明和下拉选项
  async function init() {
    await Promise.all([
      load(),
      meetingApi.admin
        .kinds()
        .then((list) => (kinds = list))
        .catch((e) => toast.error(e)),
    ]);
    loading = false;
  }

  onMount(() => {
    void init();
    api.admin
      .settings()
      .then((s) => (publicUrl = s.public_base_url))
      .catch(() => {});
    const off = events.on('asr_test', onTestEvent);
    return () => {
      off();
      for (const timer of timers.values()) clearTimeout(timer);
    };
  });

  /** 后台不认识的类型（比如开发用的模拟服务出现在正式环境）就按已保存的字段原样显示 */
  function fieldsOf(p: ProviderAdmin): FieldSpec[] {
    const known = kindMap.get(p.kind);
    if (known) return known.fields;
    const spec = (key: string, secret: boolean): FieldSpec => ({
      key,
      label: key,
      secret,
      required: false,
      default: '',
      placeholder: '',
      hint: '',
      options: [],
    });
    return [...Object.keys(p.config).map((k) => spec(k, false)), ...Object.keys(p.secrets_set).map((k) => spec(k, true))];
  }

  function display(f: FieldSpec, value: string | undefined) {
    const v = (value ?? '').trim();
    if (f.options.length) {
      const key = v || f.default;
      return f.options.find(([option]) => option === key)?.[1] ?? (key || '—');
    }
    if (!v) return f.default ? `${f.default}（默认）` : '—';
    return v;
  }

  function hintOf(f: FieldSpec, value: string | undefined) {
    const usesDefault = !f.secret && !f.options.length && f.default && !(value ?? '').trim();
    return [f.hint, usesDefault ? '留空使用默认值' : ''].filter(Boolean).join('；');
  }

  function initialConfig(f: FieldSpec, stored: string | undefined) {
    if (stored !== undefined) return stored;
    if (f.options.length) return f.default || f.options[0][0];
    return f.default;
  }

  function openCreate() {
    draft = {
      id: null,
      kind: '',
      fields: [],
      name: '',
      description: '',
      config: {},
      secrets: {},
      saved: {},
      clear: {},
      enabled: true,
      is_default: !providers.some((p) => p.enabled),
      sort_order: providers.length,
    };
  }

  function chooseKind(k: ProviderKind) {
    if (!draft) return;
    draft.kind = k.kind;
    draft.fields = k.fields;
    if (!draft.name.trim() || kinds.some((other) => other.label === draft?.name)) draft.name = k.label;
    // 新建时把默认值直接填进去，管理员能看到、也能改（比如换成新加坡地域的地址）
    draft.config = Object.fromEntries(k.fields.filter((f) => !f.secret).map((f) => [f.key, initialConfig(f, undefined)]));
    draft.secrets = Object.fromEntries(k.fields.filter((f) => f.secret).map((f) => [f.key, '']));
  }

  function openEdit(p: ProviderAdmin) {
    const fields = fieldsOf(p);
    draft = {
      id: p.id,
      kind: p.kind,
      fields,
      name: p.name,
      description: p.description,
      config: Object.fromEntries(
        fields.filter((f) => !f.secret).map((f) => [f.key, p.config[f.key] ?? (f.options.length ? initialConfig(f, undefined) : '')]),
      ),
      secrets: Object.fromEntries(fields.filter((f) => f.secret).map((f) => [f.key, ''])),
      saved: Object.fromEntries(fields.filter((f) => f.secret && p.secrets_set[f.key]).map((f) => [f.key, p.secrets_masked[f.key] || '******'])),
      clear: {},
      enabled: p.enabled,
      is_default: p.is_default,
      sort_order: p.sort_order,
    };
  }

  function payload(d: Draft) {
    const config: Record<string, string> = {};
    const secrets: Record<string, string> = {};
    for (const f of d.fields) {
      if (f.secret) {
        const value = (d.secrets[f.key] ?? '').trim();
        if (value && !d.clear[f.key]) secrets[f.key] = value;
      } else {
        // 每个字段都发：留空表示用默认值，不发会被当成“不改”
        config[f.key] = (d.config[f.key] ?? '').trim();
      }
    }
    return {
      name: d.name.trim(),
      description: d.description.trim(),
      config,
      secrets,
      enabled: d.enabled,
      is_default: d.is_default,
      sort_order: Number(d.sort_order) || 0,
    };
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (!draft || !draft.kind) return;
    // 请求期间弹窗可能被关掉（draft 变成 null），先记下编号
    const id = draft.id;
    saving = true;
    try {
      if (id === null) {
        await meetingApi.admin.createProvider({ kind: draft.kind, ...payload(draft) });
        toast.success('已添加，可以点“检查密钥”确认一下');
      } else {
        const clear = draft.clear;
        await meetingApi.admin.patchProvider(id, {
          ...payload(draft),
          clear_secrets: Object.keys(clear).filter((k) => clear[k]),
        });
        // 配置变了，之前的检查结果不再作数
        delete checks[id];
        if (!tests[id]?.running) delete tests[id];
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

  async function check(p: ProviderAdmin) {
    checks[p.id] = 'running';
    try {
      checks[p.id] = await meetingApi.admin.checkProvider(p.id);
    } catch (e) {
      checks[p.id] = { ok: false, message: errorText(e) };
    }
  }

  function armTimer(id: number) {
    clearTimeout(timers.get(id));
    timers.set(
      id,
      setTimeout(() => {
        timers.delete(id);
        const t = tests[id];
        if (t?.running) tests[id] = { ...t, running: false, ok: false, lost: true };
      }, TEST_TIMEOUT_MS),
    );
  }

  function disarmTimer(id: number) {
    clearTimeout(timers.get(id));
    timers.delete(id);
  }

  /** 每个事件都带着到目前为止的全部步骤，整体替换即可；别的标签页发起的测试也照样显示 */
  function onTestEvent(e: ProviderTestEvent) {
    const wasRunning = tests[e.provider_id]?.running;
    tests[e.provider_id] = { running: !e.done, ok: !!e.ok, steps: e.steps ?? [], lost: false };
    if (e.done) disarmTimer(e.provider_id);
    else if (!wasRunning) armTimer(e.provider_id);
  }

  async function runTest(p: ProviderAdmin) {
    const ok = await confirm({
      title: `完整测试「${p.name}」？`,
      message:
        '会生成一段 3 秒的测试音频，通过站点公网地址让服务商来拉取并识别，用来确认服务商能访问本站。' +
        '需要先在“系统设置”里填写站点公网地址。会产生一次 3 秒的识别费用，可以忽略。',
      confirmText: '开始测试',
    });
    if (!ok) return;
    // 先标成进行中再发请求：第一条进度事件可能比接口的响应先到
    tests[p.id] = { running: true, ok: false, steps: [], lost: false };
    armTimer(p.id);
    try {
      await meetingApi.admin.testProvider(p.id);
    } catch (e) {
      // 409：别的页面已经在测这个服务，接着等它的结果
      if (e instanceof ApiError && e.status === 409) {
        toast.error(e);
        return;
      }
      disarmTimer(p.id);
      tests[p.id] = {
        running: false,
        ok: false,
        steps: [{ name: '开始测试', ok: false, message: errorText(e) }],
        lost: false,
      };
    }
  }

  async function quickPatch(p: ProviderAdmin, body: Record<string, unknown>) {
    try {
      await meetingApi.admin.patchProvider(p.id, body);
      await load();
    } catch (e) {
      toast.error(e);
    }
  }

  async function remove(p: ProviderAdmin) {
    const ok = await confirm({
      title: `删除识别服务「${p.name}」？`,
      message: '已经识别完的会议会保留服务名称，不受影响；还有会议正在用它识别时不能删除。',
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await meetingApi.admin.deleteProvider(p.id);
      delete checks[p.id];
      delete tests[p.id];
      disarmTimer(p.id);
      toast.success('已删除');
      await load();
    } catch (e) {
      toast.error(e);
    }
  }
</script>

<div class="space-y-4">
  <div class="flex flex-wrap items-center gap-3">
    <p class="flex items-start gap-1.5 text-[13px] text-muted">
      <Info class="mt-0.5 size-4 shrink-0" />
      密钥加密保存在服务器数据库，不会发送到浏览器。阿里云、腾讯云的服务都需要先实名认证并开通对应产品。
    </p>
    <button class="btn btn-primary btn-sm ml-auto" onclick={openCreate}><Plus class="size-4" />添加识别服务</button>
  </div>

  {#if needsPublicUrl}
    <div class="flex gap-2.5 rounded-xl bg-warn-soft px-4 py-3 text-[13px] leading-relaxed text-warn-ink">
      <TriangleAlert class="mt-0.5 size-4 shrink-0" />
      <p>
        还没有填写站点公网地址。识别服务要从这个地址拉取录音，不填的话会议无法识别。
        <a href="/admin/settings" class="font-medium underline underline-offset-2">去系统设置填写</a>
      </p>
    </div>
  {/if}

  {#if loading}
    <div class="flex justify-center py-20"><LoaderCircle class="size-6 animate-spin text-muted" /></div>
  {:else if providers.length}
    <div class="grid gap-4 lg:grid-cols-2">
      {#each providers as p (p.id)}
        {@const result = checks[p.id]}
        {@const test = tests[p.id]}
        {@const kind = kindMap.get(p.kind)}
        <EntityCard icon={AudioWaveform} name={p.name} description={p.description} enabled={p.enabled} isDefault={p.is_default}>
          {#snippet details()}
            <dt class="text-muted">类型</dt>
            <dd class="truncate">{p.kind_label}</dd>
            {#each fieldsOf(p) as f (f.key)}
              <dt class="text-muted">{f.label}</dt>
              {#if f.secret}
                <dd class="truncate font-mono text-[12px] {p.secrets_set[f.key] ? '' : 'text-muted'}">
                  {p.secrets_set[f.key] ? p.secrets_masked[f.key] : '未设置'}
                </dd>
              {:else}
                {@const shown = display(f, p.config[f.key])}
                <dd class="truncate {f.options.length ? '' : 'font-mono text-[12px]'}" title={shown}>{shown}</dd>
              {/if}
            {/each}
            {#if kind}
              <dt class="text-muted">单次上限</dt>
              <dd>{spoken(kind.max_part_seconds * 1000)}{kind.hotwords ? ' · 支持热词' : ''}</dd>
            {/if}
          {/snippet}
          {#snippet notes()}
            {#if result && result !== 'running'}
              <ResultNote ok={result.ok} text={(result.ok ? '密钥有效' : '密钥检查未通过') + (result.message ? ` · ${result.message}` : '')} />
            {/if}

            {#if test}
              <div class="mt-3 rounded-xl bg-surface-2 px-3 py-2.5 text-[12.5px]" aria-live="polite">
                <div class="flex items-center gap-1.5 font-medium">
                  {#if test.running}
                    <LoaderCircle class="size-4 shrink-0 animate-spin text-accent" />完整测试进行中…
                  {:else if test.ok}
                    <CircleCheck class="size-4 shrink-0 text-good-ink" /><span class="text-good-ink">完整测试通过，服务商能拉取本站的录音</span>
                  {:else if test.lost}
                    <CircleX class="size-4 shrink-0 text-bad-ink" /><span class="text-bad-ink">没有收到测试结果，可能是实时连接中断了，请重试</span>
                  {:else}
                    <CircleX class="size-4 shrink-0 text-bad-ink" /><span class="text-bad-ink">完整测试未通过</span>
                  {/if}
                  {#if !test.running}
                    <button
                      class="btn btn-ghost btn-sm btn-icon -my-1 -mr-1.5 ml-auto"
                      title="收起"
                      aria-label="收起测试结果"
                      onclick={() => delete tests[p.id]}
                    >
                      <X class="size-3.5" />
                    </button>
                  {/if}
                </div>
                {#if test.steps.length || test.running}
                  <ol class="mt-2 space-y-1.5 border-t border-line pt-2">
                    {#each test.steps as s, i (i)}
                      <li class="flex gap-2">
                        {#if s.ok}
                          <CircleCheck class="mt-0.5 size-4 shrink-0 text-good-ink" />
                        {:else}
                          <CircleX class="mt-0.5 size-4 shrink-0 text-bad-ink" />
                        {/if}
                        <div class="min-w-0">
                          <p class="text-ink">{s.name}</p>
                          {#if s.message}<p class="break-words text-muted">{s.message}</p>{/if}
                        </div>
                      </li>
                    {/each}
                    {#if test.running}
                      <li class="flex gap-2 text-muted">
                        <LoaderCircle class="mt-0.5 size-4 shrink-0 animate-spin" />
                        <p>{test.steps.length ? '下一步进行中…' : '正在检查密钥…'}</p>
                      </li>
                    {/if}
                  </ol>
                {/if}
                {#if test.running}
                  <p class="mt-2 text-[12px] text-muted">
                    服务商排队时可能要等一两分钟，最多等 4 分钟。{events.connected ? '' : '实时连接已断开，结果可能收不到。'}
                  </p>
                {/if}
              </div>
            {/if}
          {/snippet}
          {#snippet actions()}
            <button class="btn btn-secondary btn-sm" disabled={result === 'running'} onclick={() => check(p)} title="只检查密钥，几秒出结果，不产生费用">
              {#if result === 'running'}<LoaderCircle class="size-3.5 animate-spin" />{:else}<KeyRound class="size-3.5" />{/if}
              检查密钥
            </button>
            <button
              class="btn btn-secondary btn-sm"
              disabled={test?.running}
              onclick={() => runTest(p)}
              title="生成 3 秒测试音频，让服务商经站点公网地址拉取并识别"
            >
              {#if test?.running}<LoaderCircle class="size-3.5 animate-spin" />{:else}<FlaskConical class="size-3.5" />{/if}
              完整测试
            </button>
            <button class="btn btn-ghost btn-sm" onclick={() => openEdit(p)}><Pencil class="size-3.5" />编辑</button>
            {#if !p.is_default && p.enabled}
              <button class="btn btn-ghost btn-sm" onclick={() => quickPatch(p, { is_default: true })}><Star class="size-3.5" />设为默认</button>
            {/if}
            <button class="btn btn-ghost btn-sm" onclick={() => quickPatch(p, { enabled: !p.enabled })}>{p.enabled ? '停用' : '启用'}</button>
            <button class="btn btn-ghost btn-sm btn-icon ml-auto text-bad-ink" title="删除" aria-label="删除" onclick={() => remove(p)}>
              <Trash2 class="size-4" />
            </button>
          {/snippet}
        </EntityCard>
      {/each}
    </div>
  {:else}
    <EmptyState
      icon={AudioWaveform}
      title="还没有配置语音识别服务"
      text="添加阿里云或腾讯云的录音文件识别服务并填好密钥，用户就能上传会议录音、生成逐字稿和纪要了。"
      action={{ label: '添加识别服务', onclick: openCreate }}
    />
  {/if}
</div>

<Modal
  open={!!draft}
  title={draft?.id !== null ? `编辑「${draft?.name}」` : draftKind ? `添加「${draftKind.label}」` : '添加识别服务'}
  description={draft && !draft.kind ? '先选择服务类型' : undefined}
  size="lg"
  onclose={() => (draft = null)}
>
  {#if draft && !draft.kind}
    <div class="space-y-2.5">
      {#each kinds as k (k.kind)}
        <button
          type="button"
          class="flex w-full items-start gap-3 rounded-xl border border-line p-3.5 text-left transition-colors hover:border-accent hover:bg-accent-soft/40"
          onclick={() => chooseKind(k)}
        >
          <span class="grid size-9 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent"><AudioWaveform class="size-4" /></span>
          <span class="min-w-0 flex-1">
            <span class="block text-[14px] font-semibold text-ink">{k.label}</span>
            {#if k.description}<span class="mt-0.5 block text-[12.5px] leading-relaxed text-muted">{k.description}</span>{/if}
            <span class="mt-2 flex flex-wrap gap-1.5 text-[11.5px]">
              <span class="rounded-full bg-surface-2 px-2 py-0.5 text-ink-2">单次上限 {spoken(k.max_part_seconds * 1000)}</span>
              {#if k.hotwords}<span class="rounded-full bg-surface-2 px-2 py-0.5 text-ink-2">支持术语热词</span>{/if}
              {#if k.speaker_count}<span class="rounded-full bg-surface-2 px-2 py-0.5 text-ink-2">可提示参会人数</span>{/if}
            </span>
          </span>
        </button>
      {:else}
        <p class="py-8 text-center text-[13px] text-muted">没有拿到可用的服务类型，请刷新页面重试</p>
      {/each}
    </div>
  {:else if draft}
    <form id="asr-form" class="space-y-4" onsubmit={save}>
      <div class="flex items-start gap-2.5 rounded-xl bg-surface-2 px-3.5 py-3 text-[12.5px]">
        <AudioWaveform class="mt-0.5 size-4 shrink-0 text-accent" />
        <div class="min-w-0 flex-1">
          <p class="font-medium text-ink">{draftKind?.label ?? draft.kind}</p>
          {#if draftKind?.description}<p class="mt-0.5 leading-relaxed text-muted">{draftKind.description}</p>{/if}
          {#if draftKind}
            <p class="mt-1 text-muted">单次上限 {spoken(draftKind.max_part_seconds * 1000)}{draftKind.hotwords ? ' · 支持术语热词' : ''}</p>
          {/if}
        </div>
        {#if draft.id === null}
          <button type="button" class="btn btn-ghost btn-sm -my-1 shrink-0" onclick={() => draft && (draft.kind = '')}>
            <ArrowLeftRight class="size-3.5" />换类型
          </button>
        {/if}
      </div>

      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label class="label" for="a-name">显示名称</label>
          <input id="a-name" class="field" required maxlength={64} placeholder="例如：腾讯云会议引擎" bind:value={draft.name} />
        </div>
        <div>
          <label class="label" for="a-desc">说明 <span class="font-normal text-muted">（用户可见）</span></label>
          <input id="a-desc" class="field" maxlength={255} placeholder="例如：适合 2 小时以内的组会" bind:value={draft.description} />
        </div>
      </div>

      {#each draft.fields as f (f.key)}
        {@const saved = draft.saved[f.key]}
        {@const hint = hintOf(f, draft.config[f.key])}
        <div>
          <label class="label" for="a-f-{f.key}">
            {f.label}{#if !f.required}<span class="font-normal text-muted">（可选）</span>{/if}
          </label>
          {#if f.options.length}
            <select id="a-f-{f.key}" class="field" bind:value={draft.config[f.key]}>
              {#each f.options as [value, text] (value)}<option {value}>{text}</option>{/each}
            </select>
          {:else if f.secret}
            <ApiKeyField
              id="a-f-{f.key}"
              bind:value={draft.secrets[f.key]}
              bind:clear={draft.clear[f.key]}
              saved={saved ?? ''}
              placeholder={f.placeholder}
              required={f.required && !saved}
            />
          {:else}
            <input
              id="a-f-{f.key}"
              class="field font-mono"
              autocomplete="off"
              spellcheck="false"
              required={f.required && !f.default}
              placeholder={f.placeholder || f.default}
              bind:value={draft.config[f.key]}
            />
          {/if}
          {#if hint}<p class="hint">{hint}</p>{/if}
        </div>
      {/each}

      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label class="label" for="a-order">排序</label>
          <input id="a-order" class="field" type="number" bind:value={draft.sort_order} />
          <p class="hint">数字小的排在前面，用户上传录音时按这个顺序列出</p>
        </div>
        <div class="divide-y divide-line">
          <Switch bind:checked={draft.enabled} label="启用" />
          <Switch bind:checked={draft.is_default} label="设为默认服务" description="用户上传录音时默认选中" />
        </div>
      </div>
    </form>
  {/if}
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={() => (draft = null)}>取消</button>
    {#if draft?.kind}
      <button class="btn btn-primary" form="asr-form" disabled={saving}>
        {#if saving}<LoaderCircle class="size-4 animate-spin" />{/if}保存
      </button>
    {/if}
  {/snippet}
</Modal>
