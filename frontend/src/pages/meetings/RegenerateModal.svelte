<script lang="ts">
  import { untrack } from 'svelte';
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
  let presetId = $state<number | null>(null);
  // 打开时预选的方案；用户没改就不发送，会议保持原来的方案
  let presetFrom = $state<number | null>(null);
  let saving = $state(false);
  let wasOpen = false;

  // 每次打开时从会议上取当前的模板和补充要求；开着的时候不被事件推送覆盖
  $effect(() => {
    if (open && !wasOpen) {
      template = meeting.minutes_template ?? meeting.template;
      extra = meeting.extra_instructions ?? '';
      presetId = presetFrom = null;
      // 顺便刷新方案列表，管理员刚加的方案也能选到；失败就用手里已有的
      void meetings.loadOptions().catch((e) => {
        if (!meetings.options) toast.error(e);
      });
    }
    wasOpen = open;
  });

  const presets = $derived(meetings.options?.presets ?? []);

  // 方案列表到了再预选：会议当前的方案还在就用它，否则用默认方案（和服务器的回退顺序一致）
  $effect(() => {
    const list = presets;
    if (!open) return;
    untrack(() => {
      if (list.some((p) => p.id === presetId)) return;
      const pick = list.find((p) => p.id === meeting.llm_preset_id) ?? list.find((p) => p.is_default) ?? list[0];
      presetId = pick?.id ?? null;
      // 以会议自己的方案为基准：它被停用而换成了默认方案时，提交会把会议也改成默认方案，详情页才对得上
      presetFrom = meeting.llm_preset_id ?? presetId;
    });
  });

  const regenerate = $derived(!!meeting.minutes_md);
  const selected = $derived(templates.find((t) => t.id === template));
  const preset = $derived(presets.find((p) => p.id === presetId));
  const presetChanged = $derived(presetId !== null && presetId !== presetFrom);
  const noPresets = $derived(!!meetings.options && !presets.length);

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (saving || noPresets) return;
    saving = true;
    try {
      const change = presetChanged && presetId !== null ? { llm_preset_id: presetId } : {};
      meetings.upsert(
        await meetingApi.runOp(meeting.id, 'minutes', { template, extra_instructions: extra.trim(), ...change }),
      );
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
      <label class="label" for="rg-preset">整理方案</label>
      {#if presets.length}
        <select id="rg-preset" class="field" bind:value={presetId}>
          {#each presets as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
        </select>
        {#if preset?.description}<p class="hint">{preset.description}</p>{/if}
        {#if presetChanged}<p class="hint">换方案后，之后的整理和对话也会按新方案进行。</p>{/if}
      {:else if noPresets}
        <p class="rounded-lg bg-surface-2 px-3 py-2 text-[13px] text-muted">
          管理员还没有配置整理方案，暂时无法生成纪要
        </p>
      {:else}
        <div class="h-[2.4rem] animate-pulse rounded-[9px] bg-surface-2"></div>
      {/if}
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
    <button class="btn btn-primary" form="minutes-regenerate-form" disabled={saving || !template || noPresets}>
      {#if saving}<LoaderCircle class="size-4 animate-spin" />{/if}{regenerate ? '重新生成' : '生成纪要'}
    </button>
  {/snippet}
</Modal>
