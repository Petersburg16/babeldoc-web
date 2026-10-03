<script lang="ts">
  import type { Snippet } from 'svelte';
  import { bytes } from '../../../lib/format';
  import { CircleCheck, Download, LoaderCircle, RotateCcw } from '../../../lib/icons';
  import { download, type OutputFile, zipOutputs } from '../../../lib/pdf/files';
  import { toast } from '../../../lib/toast.svelte';

  interface Props {
    outputs: OutputFile[];
    /** 多个结果时打包下载用的文件名 */
    zipName?: string;
    /** 结果上方的补充说明，例如压缩前后的体积 */
    summary?: Snippet;
    onreset?: () => void;
  }

  let { outputs, zipName = '处理结果.zip', summary, onreset }: Props = $props();
  let zipping = $state(false);

  async function downloadAll() {
    zipping = true;
    try {
      download(await zipOutputs(outputs, zipName));
    } catch (e) {
      toast.error(e);
    } finally {
      zipping = false;
    }
  }
</script>

<section class="card animate-pop p-4 sm:p-5" aria-live="polite">
  <div class="flex flex-wrap items-center gap-3">
    <div class="flex items-center gap-2 text-[14px] font-medium text-good-ink">
      <CircleCheck class="size-[18px]" />
      处理完成{outputs.length > 1 ? `，共 ${outputs.length} 个文件` : ''}
    </div>
    <div class="ml-auto flex flex-wrap gap-2">
      {#if onreset}
        <button class="btn btn-ghost btn-sm" onclick={onreset}><RotateCcw class="size-3.5" /> 处理新文件</button>
      {/if}
      {#if outputs.length > 1}
        <button class="btn btn-primary btn-sm" disabled={zipping} onclick={downloadAll}>
          {#if zipping}<LoaderCircle class="size-3.5 animate-spin" />{:else}<Download class="size-3.5" />{/if}
          全部下载（zip）
        </button>
      {:else if outputs.length === 1}
        <button class="btn btn-primary btn-sm" onclick={() => download(outputs[0])}>
          <Download class="size-3.5" /> 下载
        </button>
      {/if}
    </div>
  </div>
  {#if summary}
    <div class="mt-3 text-[13px] text-ink-2">{@render summary()}</div>
  {/if}
  <ul class="mt-3 max-h-80 space-y-1.5 overflow-y-auto">
    {#each outputs as out, i (i)}
      <li class="flex items-center gap-3 rounded-xl border border-line bg-surface-2/40 px-3 py-2">
        <div class="min-w-0 flex-1">
          <p class="truncate text-[13.5px] font-medium" title={out.name}>{out.name}</p>
          <p class="text-[12px] text-muted">{bytes(out.blob.size)}</p>
        </div>
        <button class="btn btn-secondary btn-sm" onclick={() => download(out)}><Download class="size-3.5" /> 下载</button>
      </li>
    {/each}
  </ul>
</section>
