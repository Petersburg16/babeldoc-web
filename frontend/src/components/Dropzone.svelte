<script lang="ts">
  import { bytes } from '../lib/format';
  import { FileText, FileUp, X } from '../lib/icons';
  import { addFiles } from '../lib/picker';
  import { toast } from '../lib/toast.svelte';

  interface Props {
    files: File[];
    maxFiles: number;
    maxMb: number;
    maxPages: number;
    retentionDays?: number;
    disabled?: boolean;
  }

  let { files = $bindable(), maxFiles, maxMb, maxPages, retentionDays = 0, disabled = false }: Props = $props();
  let dragging = $state(false);
  let input = $state<HTMLInputElement>();
  let depth = 0;

  function add(list: FileList | File[] | null) {
    if (!list || disabled) return;
    const incoming = Array.from(list);
    const pdfs = incoming.filter((f) => f.name.toLowerCase().endsWith('.pdf') || f.type === 'application/pdf');
    if (pdfs.length < incoming.length) toast.info('已忽略非 PDF 文件');
    files = addFiles(files, pdfs, { maxMb, maxFiles, tooMany: `一次最多 ${maxFiles} 个文件，多出的已忽略` });
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    depth = 0;
    dragging = false;
    add(event.dataTransfer?.files ?? null);
  }
</script>

<div class="flex h-full flex-col">
<div
  class="group relative flex min-h-56 flex-1 flex-col items-center justify-center rounded-2xl border-[1.5px] border-dashed px-6 py-8 text-center transition-colors
    {dragging ? 'border-accent bg-accent-soft' : 'border-line-strong bg-surface-2/40 hover:border-accent/60 hover:bg-accent-soft/40'}
    {disabled ? 'pointer-events-none opacity-60' : ''}"
  role="button"
  tabindex="0"
  aria-label="选择或拖入 PDF 文件"
  ondragenter={(e) => {
    e.preventDefault();
    depth += 1;
    dragging = true;
  }}
  ondragover={(e) => e.preventDefault()}
  ondragleave={() => {
    depth -= 1;
    if (depth <= 0) dragging = false;
  }}
  ondrop={onDrop}
  onclick={() => input?.click()}
  onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), input?.click())}
>
  <div class="mb-4 grid size-14 place-items-center rounded-2xl bg-surface shadow-card ring-1 ring-line transition-transform group-hover:-translate-y-0.5">
    <FileUp class="size-6 text-accent" strokeWidth={1.75} />
  </div>
  <p class="text-[15px] font-medium">
    拖拽 PDF 到这里，或 <span class="text-accent">点击选择文件</span>
  </p>
  <p class="mt-1.5 text-[12.5px] text-muted">
    单个文件不超过 {maxMb} MB · 一次最多 {maxFiles} 个 · 每个任务不超过 {maxPages} 页
  </p>
  {#if retentionDays}
    <p class="mt-1 text-[12.5px] text-muted">原文与译文保留 {retentionDays} 天，到期自动删除，请及时下载</p>
  {/if}
  <input
    bind:this={input}
    type="file"
    accept="application/pdf,.pdf"
    multiple
    class="hidden"
    onchange={(e) => {
      add(e.currentTarget.files);
      e.currentTarget.value = '';
    }}
  />
</div>

{#if files.length}
  <ul class="mt-3 max-h-72 space-y-1.5 overflow-y-auto">
    {#each files as file, index (file.name + file.size)}
      <li class="animate-pop flex items-center gap-3 rounded-xl border border-line bg-surface px-3 py-2">
        <div class="grid size-8 shrink-0 place-items-center rounded-lg bg-bad-soft text-bad-ink">
          <FileText class="size-4" />
        </div>
        <div class="min-w-0 flex-1">
          <p class="truncate text-[13.5px] font-medium" title={file.name}>{file.name}</p>
          <p class="text-[12px] text-muted">{bytes(file.size)}</p>
        </div>
        <button
          class="btn btn-ghost btn-sm btn-icon"
          aria-label="移除 {file.name}"
          {disabled}
          onclick={() => (files = files.filter((_, i) => i !== index))}
        >
          <X class="size-4" />
        </button>
      </li>
    {/each}
  </ul>
{/if}
</div>
