<script lang="ts">
  import { untrack } from 'svelte';
  import { LoaderCircle } from '../../lib/icons';
  import { ArrowLeft } from '../../lib/pdf/icons';
  import { provideTool, type ToolDef } from '../../lib/pdf/tools';

  let { tool }: { tool: ToolDef } = $props();
  // PdfApp 按工具 id 重建本组件，tool 在组件存续期间不变
  provideTool(untrack(() => tool));
  const view = $derived(tool.load());
  const Icon = $derived(tool.icon);

  $effect(() => {
    const site = document.title;
    document.title = `${tool.name} · ${site}`;
    return () => (document.title = site);
  });
</script>

<div>
  <a href="/pdf" class="inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-ink">
    <ArrowLeft class="size-3.5" /> 全部 PDF 工具
  </a>
  <header class="mt-3 mb-6 flex items-start gap-3.5">
    <div class="grid size-11 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent-ink">
      <Icon class="size-[22px]" strokeWidth={1.75} />
    </div>
    <div class="min-w-0">
      <h1 class="text-[20px] font-semibold tracking-tight">{tool.name}</h1>
      <p class="mt-0.5 text-[13.5px] text-ink-2">{tool.desc}</p>
    </div>
  </header>

  {#await view}
    <div class="flex items-center gap-2 py-16 text-[13px] text-muted">
      <LoaderCircle class="size-4 animate-spin" /> 正在加载工具…
    </div>
  {:then module}
    <module.default />
  {:catch error}
    <div class="card p-6 text-center">
      <p class="font-medium">工具加载失败</p>
      <p class="mt-1 text-[13px] text-muted">{String(error)}</p>
      <button class="btn btn-secondary mt-4" onclick={() => location.reload()}>刷新重试</button>
    </div>
  {/await}

  <p class="mt-10 text-[12px] leading-relaxed text-muted">
    文件只在你的浏览器里处理，不会上传到服务器；处理所需的程序和字体都由本站提供，首次使用后浏览器会缓存。
  </p>
</div>
