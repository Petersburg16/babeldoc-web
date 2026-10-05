<script lang="ts">
  import { untrack } from 'svelte';
  import ProgressBar from '../../components/ProgressBar.svelte';
  import {
    Ban,
    ChevronDown,
    CircleAlert,
    Clock,
    Info,
    LoaderCircle,
    RefreshCw,
    Sparkles,
  } from '../../lib/icons';
  import { AUDIO_ACCEPT, spoken } from '../../lib/meeting/format';
  import { Mic, ShieldCheck } from '../../lib/meeting/icons';
  import type { ProviderPublic } from '../../lib/meeting/types';
  import { meetings } from '../../lib/meetings.svelte';
  import FilePicker from '../pdf/ui/FilePicker.svelte';
  import { probeDuration, upload } from './uploadTask.svelte';

  interface Props {
    /** 选项（识别服务、模板、整理方案、上限）加载失败时的错误信息 */
    optionsError?: string;
    onretry?: () => void;
  }

  let { optionsError = '', onretry }: Props = $props();

  const STORAGE_KEY = 'bdw-meeting-options';

  // 0.4.0 存过 model_id（翻译模型的编号），现在不读了，下次保存时就丢掉
  interface Saved {
    provider_id: number | null;
    template: string;
    llm_preset_id: number | null;
  }

  function restore(): Saved {
    const base: Saved = { provider_id: null, template: '', llm_preset_id: null };
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
      return saved && typeof saved === 'object' ? { ...base, ...saved } : base;
    } catch {
      return base;
    }
  }

  const stem = (name: string) => name.replace(/\.[^.]+$/, '');

  const saved = restore();
  let providerId = $state<number | null>(saved.provider_id);
  let template = $state(saved.template);
  let presetId = $state<number | null>(saved.llm_preset_id);
  let files = $state<File[]>([]);
  let title = $state('');
  let autoTitle = '';
  let speakers = $state<number | null>(null);
  let extra = $state('');
  let advanced = $state(false);
  let probe = $state<{ file: File; seconds: number | null; done: boolean } | null>(null);

  const options = $derived(meetings.options);
  const providers = $derived(options?.providers ?? []);
  const templates = $derived(options?.templates ?? []);
  const presets = $derived(options?.presets ?? []);
  const provider = $derived(providers.find((p) => p.id === providerId));
  const selectedTemplate = $derived(templates.find((t) => t.id === template));
  const preset = $derived(presets.find((p) => p.id === presetId));
  const file = $derived<File | null>(files[0] ?? null);
  const probing = $derived(!!file && !(probe?.file === file && probe.done));
  const seconds = $derived(probe?.file === file ? probe.seconds : null);
  const askSpeakers = $derived(provider?.speaker_count ?? false);
  const speakersValid = $derived(
    !askSpeakers || speakers == null || (Number.isInteger(speakers) && speakers >= 1 && speakers <= 50),
  );
  const overPart = $derived(
    seconds !== null && !!provider && provider.max_part_seconds > 0 && seconds > provider.max_part_seconds,
  );
  const blocked = $derived.by((): { message: string; alternatives: ProviderPublic[] } | null => {
    if (seconds === null || !options) return null;
    if (options.max_audio_hours > 0 && seconds > options.max_audio_hours * 3600) {
      return {
        message: `这段录音长 ${spoken(seconds * 1000)}，超过了 ${options.max_audio_hours} 小时的上限，请剪短后再上传。`,
        alternatives: [],
      };
    }
    if (overPart && provider && !options.split_supported) {
      const alternatives = providers.filter(
        (p) => p.id !== provider.id && (p.max_part_seconds <= 0 || p.max_part_seconds >= seconds),
      );
      return {
        message:
          `「${provider.name}」单次最长识别 ${spoken(provider.max_part_seconds * 1000)}，这段录音长 ${spoken(seconds * 1000)}。` +
          (alternatives.length ? '请改用上限更高的服务：' : '目前没有上限更高的服务，请剪短后再上传。'),
        alternatives,
      };
    }
    return null;
  });
  const vendor = $derived(
    provider?.kind.startsWith('aliyun') ? '阿里云' : provider?.kind.startsWith('tencent') ? '腾讯云' : '阿里云/腾讯云',
  );
  const retentionDays = $derived(options?.retention_days ?? 0);
  const canSubmit = $derived(!!file && !!provider && !probing && !blocked && speakersValid && !upload.active);

  // 选了新文件就读时长；标题没改过就跟着换成新文件名
  $effect(() => {
    const current = file;
    untrack(() => {
      if (!current) {
        probe = null;
        if (title === autoTitle) title = autoTitle = '';
        return;
      }
      if (!title.trim() || title === autoTitle) title = autoTitle = stem(current.name);
      probe = { file: current, seconds: null, done: false };
    });
    if (!current) return;
    let alive = true;
    void probeDuration(current).then((value) => {
      if (alive) probe = { file: current, seconds: value, done: true };
    });
    return () => {
      alive = false;
    };
  });

  // 上次的选择失效了（服务停用、模板改名、方案停用）就换回默认
  $effect(() => {
    const list = providers;
    untrack(() => {
      if (list.length && !list.some((p) => p.id === providerId)) providerId = (list.find((p) => p.is_default) ?? list[0]).id;
    });
  });

  $effect(() => {
    const list = presets;
    untrack(() => {
      if (list.length && !list.some((p) => p.id === presetId)) presetId = (list.find((p) => p.is_default) ?? list[0]).id;
    });
  });

  $effect(() => {
    const opts = options;
    untrack(() => {
      if (opts && !opts.templates.some((t) => t.id === template)) {
        template = opts.templates.some((t) => t.id === opts.default_template) ? opts.default_template : (opts.templates[0]?.id ?? '');
      }
    });
  });

  $effect(() => {
    const value = JSON.stringify({ provider_id: providerId, template, llm_preset_id: presetId });
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch {
      /* 忽略 */
    }
  });

  function providerText(p: ProviderPublic) {
    return p.label && p.label !== p.name ? `${p.name}（${p.label}）` : p.name;
  }

  async function submit() {
    if (!file || !provider || !canSubmit) return;
    const created = await upload.start(file, {
      title: title.trim() || stem(file.name),
      provider_id: provider.id,
      // 没有可用方案时传 null：服务器照常识别，整理时再提示
      llm_preset_id: preset?.id ?? null,
      template: template || undefined,
      extra_instructions: extra.trim(),
      expected_speakers: askSpeakers ? (speakers ?? null) : null,
    });
    if (!created) return;
    files = [];
    title = autoTitle = '';
    speakers = null;
    extra = '';
  }
  function sizeLimit(mb: number) {
    return mb >= 1024 && mb % 1024 === 0 ? `${mb / 1024} GB` : `${mb} MB`;
  }
</script>

<div class="card flex flex-col p-4 sm:p-5">
  <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><Mic class="size-4 text-accent" />上传录音</h2>

  <div class="mt-4">
    <FilePicker
      bind:files
      accept={AUDIO_ACCEPT}
      kind="录音"
      maxMb={options?.max_audio_upload_mb ?? 0}
      disabled={upload.active || !options}
      hint={options
        ? `支持常见音频，也支持会议软件导出的视频；单个不超过 ${sizeLimit(options.max_audio_upload_mb)}、${options.max_audio_hours} 小时`
        : '正在读取上传设置…'}
    />
    {#if file}
      <p class="mt-2 flex items-start gap-1.5 text-[12.5px] text-muted">
        {#if probing}
          <LoaderCircle class="mt-0.5 size-3.5 shrink-0 animate-spin" />正在读取录音时长…
        {:else if seconds !== null}
          <Clock class="mt-0.5 size-3.5 shrink-0" />
          <span>
            时长 <span class="font-medium text-ink">{spoken(seconds * 1000)}</span>
            {#if overPart && options?.split_supported && !blocked}
              <span>，超过所选服务的单次上限，会自动切段识别</span>
            {/if}
          </span>
        {:else}
          <Info class="mt-0.5 size-3.5 shrink-0" />浏览器读不出这个文件的时长，上传后由服务器检查
        {/if}
      </p>
    {/if}
    {#if blocked}
      <div class="mt-3 flex gap-2 rounded-xl bg-bad-soft px-3 py-2.5 text-[12.5px] leading-relaxed text-bad-ink">
        <CircleAlert class="mt-0.5 size-4 shrink-0" />
        <div class="min-w-0">
          <p>{blocked.message}</p>
          {#if blocked.alternatives.length}
            <div class="mt-2 flex flex-wrap gap-1.5">
              {#each blocked.alternatives as alt (alt.id)}
                <button class="btn btn-secondary btn-sm" onclick={() => (providerId = alt.id)}>改用「{alt.name}」</button>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    {/if}
  </div>

  {#if optionsError && !options}
    <div class="mt-4 flex items-start gap-2 rounded-xl bg-bad-soft px-3 py-2.5 text-[12.5px] text-bad-ink">
      <CircleAlert class="mt-0.5 size-4 shrink-0" />
      <p class="min-w-0 flex-1 break-words">上传设置加载失败：{optionsError}</p>
      {#if onretry}
        <button class="btn btn-secondary btn-sm" onclick={onretry}><RefreshCw class="size-3.5" /> 重试</button>
      {/if}
    </div>
  {/if}

  <div class="mt-4">
    <label class="label" for="m-title">标题</label>
    <input
      id="m-title"
      class="field"
      maxlength="200"
      placeholder="默认使用文件名"
      bind:value={title}
      disabled={upload.active}
    />
  </div>

  <div class="mt-4">
    <label class="label" for="m-provider">识别服务</label>
    {#if providers.length}
      <select id="m-provider" class="field" bind:value={providerId} disabled={upload.active}>
        {#each providers as p (p.id)}<option value={p.id}>{providerText(p)}</option>{/each}
      </select>
      {#if provider?.description}<p class="hint">{provider.description}</p>{/if}
      {#if provider && !provider.speaker_count}<p class="hint">该服务会自动判断说话人数，不用填写人数</p>{/if}
    {:else if options}
      <p class="rounded-lg bg-surface-2 px-3 py-2 text-[13px] text-muted">管理员还没有配置语音识别服务，暂时无法上传</p>
    {:else}
      <div class="h-[2.4rem] animate-pulse rounded-[9px] bg-surface-2"></div>
    {/if}
  </div>

  {#if askSpeakers}
    <div class="mt-4">
      <label class="label" for="m-speakers">预计人数 <span class="font-normal text-muted">（可选）</span></label>
      <input
        id="m-speakers"
        type="number"
        min="1"
        max="50"
        step="1"
        inputmode="numeric"
        class="field w-32"
        placeholder="自动判断"
        bind:value={speakers}
        aria-invalid={!speakersValid}
        disabled={upload.active}
      />
      <p class="hint {speakersValid ? '' : 'text-bad-ink'}">
        {speakersValid ? '知道人数就填上，说话人区分会更准；不确定就留空' : '请填 1–50 之间的整数，或者留空'}
      </p>
    </div>
  {/if}

  <div class="mt-4">
    <label class="label" for="m-template">纪要模板</label>
    {#if templates.length}
      <select id="m-template" class="field" bind:value={template} disabled={upload.active}>
        {#each templates as t (t.id)}<option value={t.id}>{t.name}</option>{/each}
      </select>
      {#if selectedTemplate?.description}<p class="hint">{selectedTemplate.description}</p>{/if}
    {:else}
      <div class="h-[2.4rem] animate-pulse rounded-[9px] bg-surface-2"></div>
    {/if}
  </div>

  <div class="mt-4">
    <label class="label" for="m-preset">整理方案</label>
    {#if presets.length}
      <select id="m-preset" class="field" bind:value={presetId} disabled={upload.active}>
        {#each presets as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
      </select>
      {#if preset?.description}<p class="hint">{preset.description}</p>{/if}
    {:else if options}
      <p class="rounded-lg bg-surface-2 px-3 py-2 text-[13px] text-muted">
        管理员还没有配置整理方案：录音会照常识别，整理和纪要要等配置好后在会议页面重新生成。
      </p>
    {:else}
      <div class="h-[2.4rem] animate-pulse rounded-[9px] bg-surface-2"></div>
    {/if}
  </div>

  <button
    class="mt-4 flex items-center gap-1.5 self-start text-[13px] font-medium text-ink-2 hover:text-ink"
    onclick={() => (advanced = !advanced)}
    aria-expanded={advanced}
  >
    <ChevronDown class="size-4 transition-transform {advanced ? 'rotate-180' : ''}" />
    高级选项
    {#if !advanced && extra.trim()}<span class="font-normal text-muted">（已填写补充要求）</span>{/if}
  </button>

  {#if advanced}
    <div class="animate-pop mt-2 space-y-3 border-t border-line pt-3">
      <div>
        <label class="label" for="m-extra">补充要求 <span class="font-normal text-muted">（可选）</span></label>
        <textarea
          id="m-extra"
          class="field"
          rows="3"
          maxlength="2000"
          placeholder="例如：重点记录实验进展和老师的意见"
          bind:value={extra}
          disabled={upload.active}
        ></textarea>
        <p class="hint">生成纪要时交给大模型参考，之后也可以在详情页修改后重新生成</p>
      </div>
    </div>
  {/if}

  <div class="mt-4 flex gap-2 rounded-xl bg-surface-2 px-3 py-2.5 text-[12px] leading-relaxed text-ink-2">
    <ShieldCheck class="mt-0.5 size-4 shrink-0 text-muted" />
    <p>
      录音会上传到所选的识别服务（{vendor}）进行识别，整理和纪要由本站配置的大模型生成；{retentionDays > 0
        ? `录音保留 ${retentionDays} 天后自动删除，文字记录保留到你删除为止。`
        : '录音和文字记录都保留到你删除为止。'}
    </p>
  </div>

  <div class="mt-auto pt-5">
    {#if upload.active}
      <div class="rounded-xl border border-line bg-surface-2/50 p-3">
        <div class="flex items-center gap-2 text-[13px]">
          <LoaderCircle class="size-4 shrink-0 animate-spin text-accent" />
          <span class="min-w-0 flex-1 truncate" title={upload.name}>
            {upload.ratio < 1 ? '正在上传' : '正在提交'}
            <span class="font-medium">{upload.name}</span>
          </span>
          <span class="tabular font-semibold">{Math.floor(upload.ratio * 100)}%</span>
        </div>
        <div class="mt-2"><ProgressBar value={upload.ratio * 100} active label="上传进度" /></div>
        <div class="mt-2.5 flex items-center gap-3">
          <p class="min-w-0 flex-1 text-[12px] text-muted">上传时可以切到别的页面，但不要关闭或刷新浏览器</p>
          <button
            class="btn btn-secondary btn-sm shrink-0"
            disabled={upload.canceling || upload.ratio >= 1}
            onclick={() => upload.cancel()}
          >
            <Ban class="size-3.5" />{upload.canceling ? '正在取消…' : '取消上传'}
          </button>
        </div>
      </div>
    {:else}
      <button class="btn btn-primary btn-lg w-full" disabled={!canSubmit} onclick={submit}>
        {#if probing}
          <LoaderCircle class="size-4 animate-spin" /> 正在读取录音时长…
        {:else}
          <Sparkles class="size-4" /> 上传并开始识别
        {/if}
      </button>
    {/if}
  </div>
</div>
