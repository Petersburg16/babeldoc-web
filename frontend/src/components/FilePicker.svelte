<script lang="ts">
  import { bytes } from '../lib/format';
  import { ArrowDown, ArrowUp, FileUp, GripVertical, X } from '../lib/icons';
  import { addFiles } from '../lib/picker';
  import { toast } from '../lib/toast.svelte';

  interface Props {
    files: File[];
    /** <input accept>，例如 "application/pdf,.pdf" */
    accept: string;
    /** 判断文件是否可用；不传时按 accept 里的扩展名判断 */
    match?: (file: File) => boolean;
    /** 拖放区里的类型说明，例如 "PDF"、"图片" */
    kind?: string;
    multiple?: boolean;
    maxFiles?: number;
    maxMb?: number;
    reorderable?: boolean;
    disabled?: boolean;
    hint?: string;
  }

  let {
    files = $bindable(),
    accept,
    match,
    kind = 'PDF',
    multiple = false,
    maxFiles = multiple ? 50 : 1,
    maxMb = 500,
    reorderable = false,
    disabled = false,
    hint = '',
  }: Props = $props();

  let dragging = $state(false);
  let input = $state<HTMLInputElement>();
  let depth = 0;
  let dragIndex = $state<number | null>(null);
  let overIndex = $state<number | null>(null);

  const extensions = $derived(
    accept
      .split(',')
      .map((s) => s.trim().toLowerCase())
      .filter((s) => s.startsWith('.')),
  );

  function accepts(file: File) {
    if (match) return match(file);
    const name = file.name.toLowerCase();
    return extensions.some((ext) => name.endsWith(ext));
  }

  function add(list: FileList | File[] | null) {
    if (!list || disabled) return;
    const incoming = Array.from(list);
    const ok = incoming.filter(accepts);
    if (ok.length < incoming.length) toast.info(`已忽略不是${kindText}的文件`);
    const next = addFiles(files, ok, { maxMb, maxFiles, single: !multiple });
    // 单文件模式下没有可换的新文件时不重新赋值：工具页按 files 变化清空结果
    if (next !== files) files = next;
  }

  function move(from: number, to: number) {
    if (to < 0 || to >= files.length || from === to) return;
    const next = [...files];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    files = next;
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    depth = 0;
    dragging = false;
    add(event.dataTransfer?.files ?? null);
  }

  const showDrop = $derived(multiple || files.length === 0);
  // 中文与西文之间留空格：“拖拽 PDF 到这里”“拖拽图片到这里”
  const kindText = $derived(`${/^[A-Za-z0-9]/.test(kind) ? ' ' : ''}${kind}${/[A-Za-z0-9]$/.test(kind) ? ' ' : ''}`);
</script>

<div class="flex flex-col gap-3">
  {#if showDrop}
    <div
      class="group relative flex flex-col items-center justify-center rounded-2xl border-[1.5px] border-dashed px-6 text-center transition-colors
        {files.length ? 'min-h-28 py-5' : 'min-h-56 py-8'}
        {dragging ? 'border-accent bg-accent-soft' : 'border-line-strong bg-surface-2/40 hover:border-accent/60 hover:bg-accent-soft/40'}
        {disabled ? 'pointer-events-none opacity-60' : ''}"
      role="button"
      tabindex="0"
      aria-label="选择或拖入{kindText}{kind.endsWith('文件') ? '' : '文件'}"
      ondragenter={(e) => {
        if (!e.dataTransfer?.types.includes('Files')) return;
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
      {#if !files.length}
        <div class="mb-4 grid size-14 place-items-center rounded-2xl bg-surface shadow-card ring-1 ring-line transition-transform group-hover:-translate-y-0.5">
          <FileUp class="size-6 text-accent" strokeWidth={1.75} />
        </div>
      {/if}
      <p class="text-[14.5px] font-medium">
        {files.length ? `继续添加${kindText}` : `拖拽${kindText}到这里，或`}
        <span class="text-accent">点击选择文件</span>
      </p>
      {#if hint && !files.length}
        <p class="mt-1.5 text-[12.5px] text-muted">{hint}</p>
      {/if}
      <input
        bind:this={input}
        type="file"
        {accept}
        {multiple}
        class="hidden"
        onchange={(e) => {
          add(e.currentTarget.files);
          e.currentTarget.value = '';
        }}
      />
    </div>
  {/if}

  {#if files.length}
    <ul class="space-y-1.5">
      {#each files as file, index (file.name + file.size)}
        <li
          class="animate-pop flex items-center gap-2.5 rounded-xl border bg-surface px-2.5 py-2 transition-colors
            {overIndex === index && dragIndex !== null && dragIndex !== index ? 'border-accent' : 'border-line'}
            {dragIndex === index ? 'opacity-50' : ''}"
          draggable={reorderable && !disabled}
          ondragstart={(e) => {
            dragIndex = index;
            e.dataTransfer?.setData('text/plain', String(index));
          }}
          ondragover={(e) => {
            if (dragIndex === null) return;
            e.preventDefault();
            overIndex = index;
          }}
          ondrop={(e) => {
            if (dragIndex === null) return;
            e.preventDefault();
            move(dragIndex, index);
            dragIndex = overIndex = null;
          }}
          ondragend={() => (dragIndex = overIndex = null)}
        >
          {#if reorderable}
            <span class="cursor-grab text-muted" aria-hidden="true"><GripVertical class="size-4" /></span>
            <span class="w-5 text-center text-[12px] text-muted tabular">{index + 1}</span>
          {/if}
          <div class="min-w-0 flex-1">
            <p class="truncate text-[13.5px] font-medium" title={file.name}>{file.name}</p>
            <p class="text-[12px] text-muted">{bytes(file.size)}</p>
          </div>
          {#if reorderable && files.length > 1}
            <button class="btn btn-ghost btn-sm btn-icon" aria-label="上移" disabled={disabled || index === 0} onclick={() => move(index, index - 1)}>
              <ArrowUp class="size-4" />
            </button>
            <button
              class="btn btn-ghost btn-sm btn-icon"
              aria-label="下移"
              disabled={disabled || index === files.length - 1}
              onclick={() => move(index, index + 1)}
            >
              <ArrowDown class="size-4" />
            </button>
          {/if}
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
