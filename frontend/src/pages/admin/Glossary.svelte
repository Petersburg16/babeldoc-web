<script lang="ts">
  import { onMount } from 'svelte';
  import Modal from '../../components/Modal.svelte';
  import { confirm } from '../../lib/confirm.svelte';
  import { dateTime, isImeEnter } from '../../lib/format';
  import { BookA, Info, LoaderCircle, Pencil, Plus, Search, Trash2, TriangleAlert, X } from '../../lib/icons';
  import { meetingApi } from '../../lib/meeting/api';
  import { MAX_WRONG_FORMS, splitWrongForms } from '../../lib/meeting/format';
  import type { GlossaryTerm } from '../../lib/meeting/types';
  import { toast } from '../../lib/toast.svelte';

  // 与后端一致：腾讯云热词最多 128 个（按添加先后取）
  const HOTWORD_LIMIT = 128;

  interface Draft {
    id: number | null;
    term: string;
    wrong_forms: string[];
    note: string;
    pending: string;
  }

  let terms = $state<GlossaryTerm[]>([]);
  let loading = $state(true);
  let query = $state('');
  let draft = $state<Draft | null>(null);
  let saving = $state(false);

  const filtered = $derived.by(() => {
    const q = query.trim().toLowerCase();
    if (!q) return terms;
    return terms.filter((t) => [t.term, t.note, ...t.wrong_forms].some((s) => s.toLowerCase().includes(q)));
  });

  // 后端按 id 取前 128 条作热词，超出的在表里标出来
  const hotwordIds = $derived.by(() => {
    if (terms.length <= HOTWORD_LIMIT) return null;
    return new Set(
      terms
        .map((t) => t.id)
        .sort((a, b) => a - b)
        .slice(0, HOTWORD_LIMIT),
    );
  });

  async function load() {
    try {
      terms = await meetingApi.admin.glossary();
    } catch (e) {
      toast.error(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  function openCreate() {
    draft = { id: null, term: '', wrong_forms: [], note: '', pending: '' };
  }

  function openEdit(t: GlossaryTerm) {
    draft = { id: t.id, term: t.term, wrong_forms: [...t.wrong_forms], note: t.note, pending: '' };
  }

  /** 把输入框里的内容加进听错写法；用逗号、顿号、分号或换行隔开可以一次加多个 */
  function addPending() {
    if (!draft) return;
    const items = splitWrongForms(draft.pending);
    let dropped = false;
    for (const item of items) {
      if (item === draft.term.trim() || draft.wrong_forms.includes(item)) continue;
      if (draft.wrong_forms.length >= MAX_WRONG_FORMS) {
        dropped = true;
        break;
      }
      draft.wrong_forms.push(item);
    }
    draft.pending = '';
    if (dropped) toast.error(`每条术语最多 ${MAX_WRONG_FORMS} 个听错写法`);
  }

  function removeForm(index: number) {
    draft?.wrong_forms.splice(index, 1);
  }

  function onFormKeydown(event: KeyboardEvent) {
    if (!draft) return;
    if (event.key === 'Enter') {
      // 中文输入法选词时的回车不算
      if (isImeEnter(event)) return;
      event.preventDefault();
      addPending();
    } else if (event.key === 'Backspace' && !draft.pending && draft.wrong_forms.length) {
      draft.wrong_forms.pop();
    }
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (!draft) return;
    addPending(); // 输入框里还没按回车的也算上
    const body = { term: draft.term.trim(), wrong_forms: [...draft.wrong_forms], note: draft.note.trim() };
    if (!body.term) {
      toast.error('术语不能为空');
      return;
    }
    saving = true;
    try {
      if (draft.id === null) {
        await meetingApi.admin.createTerm(body);
        toast.success(`已添加“${body.term}”`);
      } else {
        await meetingApi.admin.patchTerm(draft.id, body);
        toast.success('已保存');
      }
      draft = null;
      await load();
    } catch (e) {
      toast.error(e);
    } finally {
      saving = false;
    }
  }

  async function remove(t: GlossaryTerm) {
    const ok = await confirm({
      title: `删除术语「${t.term}」？`,
      message: '已经整理好的逐字稿不受影响；之后的整理和识别不再使用这条术语。',
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await meetingApi.admin.deleteTerm(t.id);
      toast.success('已删除');
      await load();
    } catch (e) {
      toast.error(e);
    }
  }
</script>

<div class="space-y-4">
  <p class="flex items-start gap-1.5 text-[13px] text-muted">
    <Info class="mt-0.5 size-4 shrink-0" />
    整理逐字稿时大模型会按这里统一写法；腾讯云会议引擎还会把术语作为识别热词（最多 {HOTWORD_LIMIT} 个）。
  </p>

  {#if hotwordIds}
    <div class="flex gap-2.5 rounded-xl bg-warn-soft px-4 py-3 text-[13px] text-warn-ink">
      <TriangleAlert class="mt-0.5 size-4 shrink-0" />
      <p>共 {terms.length} 条术语，超过了腾讯云热词的上限：只有最早添加的 {HOTWORD_LIMIT} 条会作为识别热词，其余在表里标为“未进热词”。</p>
    </div>
  {/if}

  <div class="flex flex-wrap items-center gap-3">
    <div class="relative w-full sm:w-64">
      <Search class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
      <input class="field !h-9 pl-9" placeholder="搜索术语、听错写法或备注" aria-label="搜索术语" bind:value={query} />
    </div>
    <span class="text-[13px] text-muted">
      {query.trim() ? `找到 ${filtered.length} 条，共 ${terms.length} 条` : `共 ${terms.length} 条`}
    </span>
    <button class="btn btn-primary btn-sm ml-auto" onclick={openCreate}><Plus class="size-4" />添加术语</button>
  </div>

  {#if terms.length || loading}
    <div class="card overflow-x-auto">
      <table class="table min-w-[720px]">
        <thead>
          <tr>
            <th>术语</th>
            <th>常见听错写法</th>
            <th>备注</th>
            <th>更新时间</th>
            <th class="text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {#each filtered as t (t.id)}
            <tr>
              <td class="min-w-32">
                <p class="font-medium break-words">{t.term}</p>
                {#if hotwordIds && !hotwordIds.has(t.id)}
                  <span class="mt-0.5 inline-block rounded bg-surface-3 px-1.5 py-px text-[11px] text-muted">未进热词</span>
                {/if}
              </td>
              <td>
                {#if t.wrong_forms.length}
                  <div class="flex max-w-md flex-wrap gap-1">
                    {#each t.wrong_forms as form (form)}
                      <span class="rounded-md border border-line bg-surface-2 px-1.5 py-px text-[12px] text-ink-2">{form}</span>
                    {/each}
                  </div>
                {:else}
                  <span class="text-muted">—</span>
                {/if}
              </td>
              <td class="max-w-64 text-[12.5px] break-words text-ink-2">{t.note || '—'}</td>
              <td class="whitespace-nowrap text-ink-2">{dateTime(t.updated_at)}</td>
              <td>
                <div class="flex justify-end gap-1">
                  <button class="btn btn-ghost btn-sm btn-icon" title="编辑" aria-label="编辑" onclick={() => openEdit(t)}>
                    <Pencil class="size-4" />
                  </button>
                  <button class="btn btn-ghost btn-sm btn-icon text-bad-ink" title="删除" aria-label="删除" onclick={() => remove(t)}>
                    <Trash2 class="size-4" />
                  </button>
                </div>
              </td>
            </tr>
          {:else}
            <tr>
              <td colspan="5" class="py-12 text-center text-muted">{loading ? '加载中…' : `没有找到与“${query.trim()}”相关的术语`}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else}
    <div class="card flex flex-col items-center px-6 py-14 text-center">
      <div class="grid size-14 place-items-center rounded-2xl bg-accent-soft text-accent"><BookA class="size-6" /></div>
      <p class="mt-4 font-medium">术语表还是空的</p>
      <p class="mt-1 max-w-sm text-[13px] text-muted">
        把课题组常说的专有名词、人名、缩写加进来（比如模型名、项目代号），并写上识别常听错的写法，整理出的逐字稿和纪要会更准确。
      </p>
      <button class="btn btn-primary mt-5" onclick={openCreate}><Plus class="size-4" />添加术语</button>
    </div>
  {/if}
</div>

<Modal open={!!draft} title={draft?.id === null ? '添加术语' : `编辑「${draft?.term}」`} onclose={() => (draft = null)}>
  {#if draft}
    <form id="term-form" class="space-y-4" onsubmit={save}>
      <div>
        <label class="label" for="g-term">术语</label>
        <input id="g-term" class="field" required maxlength={64} placeholder="例如：消融实验" bind:value={draft.term} />
        <p class="hint">按希望出现在逐字稿和纪要里的写法填写</p>
      </div>
      <div>
        <label class="label" for="g-forms">常见听错写法 <span class="font-normal text-muted">（可选）</span></label>
        <div
          class="flex min-h-[2.4rem] flex-wrap items-center gap-1.5 rounded-[9px] border border-line-strong bg-surface px-2 py-1.5 focus-within:border-accent focus-within:ring-3 focus-within:ring-accent-soft"
        >
          {#each draft.wrong_forms as form, i (form)}
            <span class="inline-flex items-center gap-0.5 rounded-md bg-surface-2 py-0.5 pr-0.5 pl-2 text-[12.5px] text-ink-2">
              {form}
              <button
                type="button"
                class="grid size-5 place-items-center rounded text-muted hover:bg-surface-3 hover:text-ink"
                aria-label="移除 {form}"
                onclick={() => removeForm(i)}
              >
                <X class="size-3" />
              </button>
            </span>
          {/each}
          <input
            id="g-forms"
            class="h-7 min-w-32 flex-1 bg-transparent px-1 text-[14px] text-ink outline-none placeholder:text-muted"
            maxlength={300}
            autocomplete="off"
            disabled={draft.wrong_forms.length >= MAX_WRONG_FORMS}
            placeholder={draft.wrong_forms.length >= MAX_WRONG_FORMS ? `最多 ${MAX_WRONG_FORMS} 个` : '输入后按回车添加'}
            bind:value={draft.pending}
            onkeydown={onFormKeydown}
            onblur={addPending}
          />
        </div>
        <p class="hint">识别服务容易写错成的样子，例如“小荣实验”。可以用逗号或顿号隔开，一次加多个</p>
      </div>
      <div>
        <label class="label" for="g-note">备注 <span class="font-normal text-muted">（可选）</span></label>
        <input id="g-note" class="field" maxlength={255} placeholder="例如：ablation study" bind:value={draft.note} />
      </div>
    </form>
  {/if}
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={() => (draft = null)}>取消</button>
    <button class="btn btn-primary" form="term-form" disabled={saving}>
      {#if saving}<LoaderCircle class="size-4 animate-spin" />{/if}保存
    </button>
  {/snippet}
</Modal>
