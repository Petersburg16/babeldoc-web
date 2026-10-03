<script lang="ts">
  import { onDestroy, onMount, untrack } from 'svelte';
  import Dropzone from '../components/Dropzone.svelte';
  import JobCard from '../components/JobCard.svelte';
  import ProgressBar from '../components/ProgressBar.svelte';
  import Segmented from '../components/Segmented.svelte';
  import Switch from '../components/Switch.svelte';
  import { api, uploadJobs } from '../lib/api';
  import { greeting } from '../lib/format';
  import {
    ArrowLeftRight,
    BookOpenText,
    ChevronDown,
    Languages,
    LoaderCircle,
    Megaphone,
    RefreshCw,
    Sparkles,
    X,
  } from '../lib/icons';
  import { type JobFilter, jobs } from '../lib/jobs.svelte';
  import { session } from '../lib/session.svelte';
  import { toast } from '../lib/toast.svelte';
  import type { Job, JobOptions, ModelPublic } from '../lib/types';

  const STORAGE_KEY = 'bdw-options';

  function defaults(): JobOptions {
    return {
      lang_in: session.meta?.default_lang_in ?? 'en',
      lang_out: session.meta?.default_lang_out ?? 'zh-CN',
      model_id: null,
      term_model_id: null,
      pages: null,
      output: 'both',
      dual_mode: 'side_by_side',
      dual_translate_first: false,
      translate_table_text: false,
      enhance_compatibility: false,
      ocr_workaround: false,
      auto_enable_ocr_workaround: true,
      skip_scanned_detection: false,
      primary_font_family: null,
      only_include_translated_page: false,
      auto_extract_glossary: true,
      custom_system_prompt: null,
    };
  }

  function restore(): JobOptions {
    const base = defaults();
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
      return { ...base, ...saved, pages: null };
    } catch {
      return base;
    }
  }

  let options = $state<JobOptions>(restore());
  let files = $state<File[]>([]);
  let glossary = $state<File | null>(null);
  let models = $state<ModelPublic[]>([]);
  let advanced = $state(false);
  let uploading = $state(false);
  let uploadRatio = $state(0);
  let now = $state(Date.now());
  let ticker: ReturnType<typeof setInterval> | undefined;

  const languages = $derived(session.meta?.languages ?? []);
  const usage = $derived(session.me?.usage);
  const quotaRatio = $derived(usage && usage.quota ? Math.min(100, (usage.month_pages / usage.quota) * 100) : 0);
  const selectedModel = $derived(models.find((m) => m.id === options.model_id));
  const filters: { value: JobFilter; label: string }[] = [
    { value: 'all', label: '全部' },
    { value: 'active', label: '进行中' },
    { value: 'succeeded', label: '已完成' },
    { value: 'failed', label: '失败/取消' },
  ];
  let filter = $state<JobFilter>(jobs.filter);

  $effect(() => {
    const next = filter;
    untrack(() => jobs.setFilter(next));
  });

  $effect(() => {
    const { pages: _pages, ...persist } = options;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(persist));
    } catch {
      /* 忽略 */
    }
  });

  onMount(async () => {
    ticker = setInterval(() => (now = Date.now()), 1000);
    jobs.connect(onFinish);
    if (!jobs.loaded) void jobs.load().catch((e) => toast.error(e));
    try {
      models = await api.models();
      if (!models.some((m) => m.id === options.model_id)) {
        options.model_id = (models.find((m) => m.is_default) ?? models[0])?.id ?? null;
      }
      if (options.term_model_id !== null && !models.some((m) => m.id === options.term_model_id)) {
        options.term_model_id = null;
      }
    } catch (e) {
      toast.error(e);
    }
  });

  onDestroy(() => clearInterval(ticker));

  function onFinish(job: Job) {
    if (job.status === 'succeeded') toast.success(`「${job.filename}」翻译完成`);
    else toast.push('error', `「${job.filename}」翻译失败：${job.error ?? ''}`);
    void session.refreshMe();
  }

  function swapLanguages() {
    [options.lang_in, options.lang_out] = [options.lang_out, options.lang_in];
  }

  const glossaryTemplate =
    'data:text/csv;charset=utf-8,' +
    encodeURIComponent('﻿source,target,tgt_lng\nattention,注意力,zh-CN\nlarge language model,大语言模型,zh-CN\n');

  async function submit() {
    if (!files.length) return;
    if (!options.model_id) {
      toast.error('管理员还没有配置可用的翻译模型');
      return;
    }
    const form = new FormData();
    for (const file of files) form.append('files', file, file.name);
    form.append('options', JSON.stringify({ ...options, pages: options.pages?.trim() || null }));
    if (glossary) form.append('glossary', glossary, glossary.name);
    uploading = true;
    uploadRatio = 0;
    try {
      const created = await uploadJobs(form, (r) => (uploadRatio = r));
      for (const job of created.slice().reverse()) jobs.upsert(job);
      toast.success(created.length > 1 ? `已提交 ${created.length} 个任务` : '已提交，开始排队翻译');
      files = [];
      options.pages = null;
      void session.refreshMe();
    } catch (e) {
      toast.error(e);
    } finally {
      uploading = false;
    }
  }
</script>

<div class="space-y-8">
  <section class="flex flex-wrap items-end justify-between gap-4">
    <div>
      <h1 class="text-[22px] font-semibold tracking-tight sm:text-2xl">
        {greeting()}，{session.user?.display_name}
      </h1>
      <p class="mt-1 text-[13.5px] text-muted">上传论文 PDF，保留公式、图表与版式，生成双语对照和纯译文。</p>
    </div>
    {#if usage}
      <div class="w-full max-w-64 sm:w-64">
        <div class="flex items-baseline justify-between text-[12.5px]">
          <span class="text-muted">本月已翻译</span>
          <span class="font-medium">
            {usage.month_pages}
            <span class="font-normal text-muted">/ {usage.quota ? `${usage.quota} 页` : '不限'}</span>
          </span>
        </div>
        {#if usage.quota}
          <div class="mt-1.5">
            <ProgressBar
              value={quotaRatio}
              tone={quotaRatio >= 95 ? 'bad' : quotaRatio >= 80 ? 'warn' : 'accent'}
              label="本月额度使用"
            />
          </div>
        {/if}
      </div>
    {/if}
  </section>

  {#if session.meta?.announcement}
    <div class="flex gap-3 rounded-2xl border border-accent/25 bg-accent-soft px-4 py-3 text-[13.5px] leading-relaxed">
      <Megaphone class="mt-0.5 size-4 shrink-0 text-accent" />
      <p class="whitespace-pre-line text-ink">{session.meta.announcement}</p>
    </div>
  {/if}

  <section class="grid gap-5 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
    <div class="card flex flex-col p-4 sm:p-5">
      <Dropzone
        bind:files
        maxFiles={session.meta?.max_files ?? 10}
        maxMb={session.meta?.max_upload_mb ?? 50}
        maxPages={session.meta?.max_pages_per_job ?? 300}
        retentionDays={session.meta?.file_retention_days ?? 0}
        disabled={uploading}
      />
    </div>

    <div class="card flex flex-col p-4 sm:p-5">
      <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><Languages class="size-4 text-accent" />翻译设置</h2>

      <div class="mt-4 grid grid-cols-[1fr_auto_1fr] items-end gap-2">
        <div>
          <label class="label" for="lang-in">原文语言</label>
          <select id="lang-in" class="field" bind:value={options.lang_in}>
            {#each languages as lang (lang.code)}<option value={lang.code}>{lang.label}</option>{/each}
          </select>
        </div>
        <button class="btn btn-ghost btn-icon mb-0.5" onclick={swapLanguages} aria-label="交换语言" title="交换语言">
          <ArrowLeftRight class="size-4" />
        </button>
        <div>
          <label class="label" for="lang-out">译文语言</label>
          <select id="lang-out" class="field" bind:value={options.lang_out}>
            {#each languages as lang (lang.code)}<option value={lang.code}>{lang.label}</option>{/each}
          </select>
        </div>
      </div>

      <div class="mt-4">
        <label class="label" for="model">翻译模型</label>
        {#if models.length}
          <select id="model" class="field" bind:value={options.model_id}>
            {#each models as model (model.id)}<option value={model.id}>{model.name}</option>{/each}
          </select>
          {#if selectedModel?.description}<p class="hint">{selectedModel.description}</p>{/if}
        {:else}
          <p class="rounded-lg bg-surface-2 px-3 py-2 text-[13px] text-muted">暂无可用模型，请联系管理员配置</p>
        {/if}
      </div>

      <div class="mt-4">
        <span class="label">输出内容</span>
        <Segmented
          bind:value={options.output}
          ariaLabel="输出内容"
          options={[
            { value: 'both', label: '双语 + 译文' },
            { value: 'dual', label: '仅双语对照' },
            { value: 'mono', label: '仅译文' },
          ]}
        />
      </div>

      <div class="mt-4">
        <span class="label">双语排版</span>
        <Segmented
          bind:value={options.dual_mode}
          ariaLabel="双语排版"
          options={[
            { value: 'side_by_side', label: '左右对照', disabled: options.output === 'mono' },
            { value: 'alternating', label: '原文译文交替页', disabled: options.output === 'mono' },
          ]}
        />
      </div>

      <button
        class="mt-4 flex items-center gap-1.5 self-start text-[13px] font-medium text-ink-2 hover:text-ink"
        onclick={() => (advanced = !advanced)}
        aria-expanded={advanced}
      >
        <ChevronDown class="size-4 transition-transform {advanced ? 'rotate-180' : ''}" />
        高级选项
      </button>

      {#if advanced}
        <div class="animate-pop mt-2 space-y-3 border-t border-line pt-3">
          <div>
            <label class="label" for="pages">页码范围 <span class="font-normal text-muted">（可选）</span></label>
            <input id="pages" class="field" placeholder="如 1-5,8,10-  留空表示全部" bind:value={options.pages} />
          </div>
          <div class="divide-y divide-line">
            <Switch bind:checked={options.auto_extract_glossary} label="自动提取术语" description="先让模型统一专业术语，再翻译正文，结果附带术语表" />
            <Switch bind:checked={options.translate_table_text} label="翻译表格中的文字" badge="实验" />
            <Switch bind:checked={options.enhance_compatibility} label="兼容模式" description="排版错乱或报错时开启：跳过清理、关闭富文本，译文页在前" />
            <Switch bind:checked={options.auto_enable_ocr_workaround} label="扫描件自动兼容" description="检测到扫描件（带 OCR 文字层）时自动处理" />
            <Switch bind:checked={options.dual_translate_first} label="双语中译文页在前" disabled={options.output === 'mono'} />
            <Switch bind:checked={options.only_include_translated_page} label="只输出所选页" description="配合页码范围使用" disabled={!options.pages} />
          </div>
          <div>
            <label class="label" for="term-model">术语提取模型</label>
            <select id="term-model" class="field" bind:value={options.term_model_id} disabled={!options.auto_extract_glossary}>
              <option value={null}>与翻译模型相同</option>
              {#each models as model (model.id)}<option value={model.id}>{model.name}</option>{/each}
            </select>
            <p class="hint">{options.auto_extract_glossary ? '开工前用它统一专业术语，可以和翻译模型不同' : '开启“自动提取术语”后可选'}</p>
          </div>
          <div>
            <label class="label" for="font">译文字体</label>
            <select id="font" class="field" bind:value={options.primary_font_family}>
              <option value={null}>自动</option>
              <option value="serif">衬线（宋体风格）</option>
              <option value="sans-serif">无衬线（黑体风格）</option>
              <option value="script">手写（楷体风格）</option>
            </select>
          </div>
          <div>
            <label class="label" for="prompt">自定义提示词 <span class="font-normal text-muted">（可选）</span></label>
            <textarea id="prompt" class="field" rows="3" maxlength="2000" placeholder="例如：这是一篇气象学论文，请使用该领域的规范术语" bind:value={options.custom_system_prompt}></textarea>
          </div>
          <div>
            <span class="label">术语表 CSV <span class="font-normal text-muted">（可选）</span></span>
            {#if glossary}
              <div class="flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-[13px]">
                <BookOpenText class="size-4 text-muted" />
                <span class="min-w-0 flex-1 truncate">{glossary.name}</span>
                <button class="btn btn-ghost btn-sm btn-icon" onclick={() => (glossary = null)} aria-label="移除术语表"><X class="size-4" /></button>
              </div>
            {:else}
              <label class="btn btn-secondary btn-sm cursor-pointer">
                选择文件
                <input type="file" accept=".csv,text/csv" class="hidden" onchange={(e) => (glossary = e.currentTarget.files?.[0] ?? null)} />
              </label>
              <a class="ml-2 text-[12.5px] text-accent hover:underline" href={glossaryTemplate} download="glossary-template.csv">下载模板</a>
            {/if}
          </div>
        </div>
      {/if}

      <div class="mt-auto pt-5">
        <button class="btn btn-primary btn-lg w-full" disabled={!files.length || uploading || !models.length} onclick={submit}>
          {#if uploading}
            <LoaderCircle class="size-4 animate-spin" />
            {uploadRatio < 1 ? `上传中 ${Math.round(uploadRatio * 100)}%` : '正在创建任务…'}
          {:else}
            <Sparkles class="size-4" />
            {files.length > 1 ? `开始翻译 ${files.length} 个文件` : '开始翻译'}
          {/if}
        </button>
      </div>
    </div>
  </section>

  <section>
    <div class="mb-4 flex flex-wrap items-center gap-3">
      <h2 class="text-[17px] font-semibold tracking-tight">我的任务</h2>
      <span class="text-[13px] text-muted">{jobs.total} 个</span>
      <div class="ml-auto flex items-center gap-2">
        <div class="w-[19rem] max-w-full"><Segmented bind:value={filter} options={filters} size="sm" ariaLabel="任务筛选" /></div>
        <button class="btn btn-ghost btn-sm btn-icon" onclick={() => jobs.load().catch((e) => toast.error(e))} aria-label="刷新" title="刷新">
          <RefreshCw class="size-4 {jobs.loading ? 'animate-spin' : ''}" />
        </button>
      </div>
    </div>

    {#if jobs.items.length}
      <div class="space-y-3">
        {#each jobs.items as job (job.id)}
          <JobCard {job} {now} />
        {/each}
      </div>
      {#if jobs.hasMore}
        <div class="mt-4 flex justify-center">
          <button class="btn btn-secondary" disabled={jobs.loading} onclick={() => jobs.load(true)}>加载更多</button>
        </div>
      {/if}
    {:else if jobs.loaded}
      <div class="card flex flex-col items-center px-6 py-14 text-center">
        <div class="grid size-14 place-items-center rounded-2xl bg-surface-2">
          <Languages class="size-6 text-muted" strokeWidth={1.5} />
        </div>
        <p class="mt-4 font-medium">{filter === 'all' ? '还没有翻译任务' : '这里暂时是空的'}</p>
        <p class="mt-1 text-[13px] text-muted">
          {filter === 'all' ? '把第一篇论文拖到上面试试，翻译进度会实时显示在这里。' : '换个筛选条件看看。'}
        </p>
      </div>
    {:else}
      <div class="space-y-3">
        {#each [0, 1] as i (i)}
          <div class="card h-28 animate-pulse bg-surface-2/50"></div>
        {/each}
      </div>
    {/if}
  </section>
</div>
