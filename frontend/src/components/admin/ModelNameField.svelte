<script lang="ts">
  import { errorText } from '../../lib/format';
  import { LoaderCircle } from '../../lib/icons';

  /** 模型名输入框和“拉取列表”按钮（翻译模型、会议模型共用）；怎么拉取由页面传入，拉到的模型名放进 datalist 供选择 */
  interface Props {
    id: string;
    value: string;
    /** datalist 的 id；别的输入框（比如术语提取模型）也可以用 list 指向它 */
    listId: string;
    placeholder: string;
    maxlength?: number;
    probe: () => Promise<string[]>;
  }

  let { id, value = $bindable(), listId, placeholder, maxlength, probe }: Props = $props();

  const TONES = { good: 'text-good-ink', bad: 'text-bad-ink', info: 'text-muted' };

  let names = $state<string[]>([]);
  let probing = $state(false);
  let note = $state<{ tone: keyof typeof TONES; text: string } | null>(null);

  async function load() {
    probing = true;
    note = null;
    try {
      names = await probe();
      note = names.length
        ? { tone: 'good', text: `拉取到 ${names.length} 个模型，可在“模型名”里选择` }
        : { tone: 'info', text: '接口没有返回模型列表，请手动填写' };
    } catch (e) {
      note = { tone: 'bad', text: errorText(e) };
    } finally {
      probing = false;
    }
  }
</script>

<div class="flex gap-2">
  <input {id} class="field font-mono" required {maxlength} autocomplete="off" spellcheck="false" list={listId} {placeholder} bind:value />
  <button type="button" class="btn btn-secondary shrink-0" disabled={probing} onclick={load}>
    {#if probing}<LoaderCircle class="size-4 animate-spin" />{/if}拉取列表
  </button>
</div>
<datalist id={listId}>
  {#each names as name (name)}<option value={name}></option>{/each}
</datalist>
{#if note}<p class="hint {TONES[note.tone]}">{note.text}</p>{/if}
