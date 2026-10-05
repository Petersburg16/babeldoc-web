<script lang="ts">
  import Modal from '../../components/Modal.svelte';
  import { LoaderCircle } from '../../lib/icons';
  import { meetingApi } from '../../lib/meeting/api';
  import type { MeetingDetail, MinutesTemplate } from '../../lib/meeting/types';
  import { meetings } from '../../lib/meetings.svelte';
  import { toast } from '../../lib/toast.svelte';

  interface Props {
    open: boolean;
    meeting: MeetingDetail;
    templates: MinutesTemplate[];
    onclose: () => void;
  }

  let { open, meeting, templates, onclose }: Props = $props();

  let template = $state('');
  let extra = $state('');
  let saving = $state(false);
  let wasOpen = false;

  // 每次打开时从会议上取当前的模板和补充要求；开着的时候不被事件推送覆盖
  $effect(() => {
    if (open && !wasOpen) {
      template = meeting.minutes_template ?? meeting.template;
      extra = meeting.extra_instructions ?? '';
    }
    wasOpen = open;
  });

  const regenerate = $derived(!!meeting.minutes_md);
  const selected = $derived(templates.find((t) => t.id === template));

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (saving) return;
    saving = true;
    try {
      meetings.upsert(await meetingApi.runOp(meeting.id, 'minutes', { template, extra_instructions: extra.trim() }));
      toast.success(regenerate ? '已开始重新生成纪要' : '已开始生成纪要');
      onclose();
    } catch (e) {
      toast.error(e);
    } finally {
      saving = false;
    }
  }
</script>

<Modal
  {open}
  title={regenerate ? '重新生成纪要' : '生成纪要'}
  description={regenerate ? '按选定的模板和要求重新整理，当前纪要会被替换。' : '按选定的模板和要求整理这次会议。'}
  {onclose}
>
  <form id="minutes-regenerate-form" class="space-y-4" onsubmit={submit}>
    <div>
      <label class="label" for="rg-template">纪要模板</label>
      <select id="rg-template" class="field" bind:value={template} disabled={!templates.length}>
        {#each templates as item (item.id)}<option value={item.id}>{item.name}</option>{/each}
      </select>
      {#if selected?.description}<p class="hint">{selected.description}</p>{/if}
    </div>
    <div>
      <label class="label" for="rg-extra">补充要求 <span class="font-normal text-muted">（可选）</span></label>
      <textarea
        id="rg-extra"
        class="field"
        rows="4"
        maxlength={2000}
        placeholder="例如：重点整理实验方案的讨论；待办按负责人分组；不要写寒暄部分"
        bind:value={extra}
      ></textarea>
      <p class="hint">会和模板一起交给大模型，保存后下次生成也会沿用。</p>
    </div>
  </form>
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={onclose}>取消</button>
    <button class="btn btn-primary" form="minutes-regenerate-form" disabled={saving || !template}>
      {#if saving}<LoaderCircle class="size-4 animate-spin" />{/if}{regenerate ? '重新生成' : '生成纪要'}
    </button>
  {/snippet}
</Modal>
