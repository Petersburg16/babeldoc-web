<script lang="ts">
  import FilePicker from '../../../components/FilePicker.svelte';
  import Switch from '../../../components/Switch.svelte';
  import { errorText } from '../../../lib/format';
  import { pdfBlob, readBytes, renamed, type Report } from '../../../lib/pdf/files';
  import { rangeError } from '../../../lib/pdf/ranges';
  import ToolFrame from '../ui/ToolFrame.svelte';

  let files = $state<File[]>([]);
  let partial = $state(false);
  let ranges = $state<Record<string, string>>({});

  const key = (f: File) => f.name + f.size;
  const badRange = $derived(partial && files.some((f) => ranges[key(f)]?.trim() && rangeError(ranges[key(f)])));

  async function run(report: Report) {
    const [{ countPages, mergePdfs, toQpdfRange }, { passwordFor }] = await Promise.all([
      import('../../../lib/pdf/ops/pages'),
      import('../../../lib/pdf/input'),
    ]);
    const inputs = [];
    for (const [i, file] of files.entries()) {
      report((i / files.length) * 0.5, `读取「${file.name}」`);
      const bytes = await readBytes(file);
      const password = await passwordFor(file, bytes);
      const pageCount = await countPages(bytes, password);
      const pages = partial ? ranges[key(file)]?.trim() : '';
      if (pages) {
        try {
          toQpdfRange(pages, pageCount);
        } catch (e) {
          throw new Error(`「${file.name}」：${errorText(e)}`);
        }
      }
      inputs.push({ bytes, password, pageCount, pages });
    }
    report(null, '正在合并');
    const out = await mergePdfs(inputs);
    return [{ name: renamed(files[0].name, '合并'), blob: pdfBlob(out) }];
  }
</script>

<ToolFrame
  resetKey={files}
  runLabel={files.length >= 2 ? `合并 ${files.length} 个文件` : '合并 PDF'}
  canRun={files.length >= 2 && !badRange}
  blocked={badRange ? '页码范围写法有误' : '至少选择两个 PDF'}
  onrun={run}
  onreset={() => {
    files = [];
    ranges = {};
  }}
>
  {#snippet input()}
    <FilePicker
      bind:files
      accept="application/pdf,.pdf"
      multiple
      reorderable
      hint="可一次选择多个文件，拖动或用箭头调整合并顺序"
    />
  {/snippet}
  {#snippet options()}
    <Switch bind:checked={partial} label="只取部分页" description="为每个文件分别填写页码，例如 1-3,5,8-" />
    {#if partial && files.length}
      <div class="space-y-2.5">
        {#each files as file (key(file))}
          {@const error = ranges[key(file)]?.trim() ? rangeError(ranges[key(file)]) : ''}
          <div>
            <label class="mb-1 block truncate text-[12.5px] text-ink-2" for="range-{key(file)}" title={file.name}>{file.name}</label>
            <input
              id="range-{key(file)}"
              class="field font-mono text-[13px]"
              placeholder="全部页"
              bind:value={ranges[key(file)]}
              aria-invalid={!!error}
            />
            {#if error}<p class="mt-1 text-[12px] text-bad-ink">{error}</p>{/if}
          </div>
        {/each}
      </div>
    {/if}
  {/snippet}
</ToolFrame>
