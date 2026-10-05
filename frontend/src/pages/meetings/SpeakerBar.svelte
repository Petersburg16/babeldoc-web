<script lang="ts">
  import Modal from '../../components/Modal.svelte';
  import { confirm } from '../../lib/confirm.svelte';
  import { LoaderCircle, Sparkles, Users } from '../../lib/icons';
  import { meetingApi } from '../../lib/meeting/api';
  import { clock, isActive, resolveSpeaker, speakerName, speakerTone, spoken } from '../../lib/meeting/format';
  import { Merge, Play, UserRoundPen } from '../../lib/meeting/icons';
  import type { MeetingDetail, Segment, SpeakerGuess } from '../../lib/meeting/types';
  import { meetings } from '../../lib/meetings.svelte';
  import { toast } from '../../lib/toast.svelte';

  interface Props {
    meeting: MeetingDetail;
    segments: Segment[];
    onseek: (ms: number) => void;
    /** 合并、撤销合并会在后端改句子的说话人（不改逐字稿版本号），之后让详情页重拉一次逐字稿 */
    onrefresh: () => void;
  }

  let { meeting, segments, onseek, onrefresh }: Props = $props();

  interface Stat {
    count: number;
    ms: number;
    first: number;
  }

  const CONFIDENCE: Record<SpeakerGuess['confidence'], string> = { high: '把握大', medium: '把握一般', low: '把握小' };

  let busy = $state(false);
  let editId = $state<string | null>(null);
  let nameDraft = $state('');
  let mergeTarget = $state('');

  const speakers = $derived(meeting.speakers ?? {});
  const locked = $derived(isActive(meeting));
  const canRerun = $derived(meeting.status === 'done' && !meeting.op && segments.length > 0);

  // 按最终说话人（沿合并关系）统计句数和时长
  const stats = $derived.by(() => {
    const total = new Map<string, Stat>();
    const cache = new Map<string, string>();
    for (const seg of segments) {
      let who = cache.get(seg.speaker);
      if (who === undefined) {
        who = resolveSpeaker(speakers, seg.speaker);
        cache.set(seg.speaker, who);
      }
      const stat = total.get(who) ?? { count: 0, ms: 0, first: seg.start_ms };
      stat.count += 1;
      stat.ms += Math.max(0, seg.end_ms - seg.start_ms);
      stat.first = Math.min(stat.first, seg.start_ms);
      total.set(who, stat);
    }
    return { total };
  });

  const ids = $derived.by(() => {
    const all = new Set(Object.keys(speakers));
    for (const seg of segments) all.add(seg.speaker);
    return [...all].sort((a, b) => number(a) - number(b));
  });
  const active = $derived(
    ids.filter(
      (id) => resolveSpeaker(speakers, id) === id && (!segments.length || (stats.total.get(id)?.count ?? 0) > 0),
    ),
  );
  const merged = $derived(ids.filter((id) => resolveSpeaker(speakers, id) !== id));
  // 建议列表只列还没命名的（和后端“全部采纳不覆盖已命名的”一致）；已命名的建议在编辑弹窗里看
  const guesses = $derived(active.filter((id) => !speakers[id]?.name?.trim() && pendingGuess(id) !== null));
  const hints = $derived(
    active.filter((id) => {
      const hint = speakers[id]?.merge_hint;
      if (!hint?.with || !(hint.with in speakers)) return false;
      return resolveSpeaker(speakers, hint.with) !== id;
    }),
  );

  const editing = $derived(editId === null ? null : { id: editId, info: speakers[editId], stat: stats.total.get(editId) });
  const editGuess = $derived(editId === null ? null : pendingGuess(editId));
  const mergeTargets = $derived(editId === null ? [] : active.filter((id) => id !== editId));

  function number(id: string) {
    return Number(id.replace(/^S/, '')) || 0;
  }

  /** 说话人自己的名字（不沿合并关系），给“已并入”这类需要区分两位的地方用 */
  function ownName(id: string) {
    return speakers[id]?.name?.trim() || `说话人 ${id.replace(/^S/, '')}`;
  }

  function pendingGuess(id: string): SpeakerGuess | null {
    const info = speakers[id];
    const guess = info?.guess;
    if (!guess?.name?.trim() || info.merged_into) return null;
    return guess.name.trim() === (info.name ?? '').trim() ? null : guess;
  }

  async function run<T>(action: () => Promise<T>) {
    busy = true;
    try {
      return await action();
    } catch (e) {
      toast.error(e);
      return undefined;
    } finally {
      busy = false;
    }
  }

  function openEdit(id: string) {
    editId = id;
    nameDraft = speakers[id]?.name ?? '';
    mergeTarget = active.find((x) => x !== id) ?? '';
  }

  async function rename(event: SubmitEvent) {
    event.preventDefault();
    const id = editId;
    if (!id) return;
    const name = nameDraft.trim();
    if (name === (speakers[id]?.name ?? '').trim()) {
      editId = null;
      return;
    }
    const done = await run(async () => {
      meetings.upsert(await meetingApi.renameSpeaker(meeting.id, id, name));
      return true;
    });
    if (done) {
      editId = null;
      toast.success(name ? `已改名为「${name}」` : '已恢复默认名字');
    }
  }

  async function merge(source: string, target: string) {
    const count = stats.total.get(source)?.count ?? 0;
    const ok = await confirm({
      title: `把「${ownName(source)}」合并到「${speakerName(speakers, target)}」？`,
      message: `「${ownName(source)}」的 ${count} 句发言都会算作「${speakerName(speakers, target)}」说的。纪要里的名字随之更新，不用重新生成；之后可以撤销。`,
      confirmText: '合并',
    });
    if (!ok) return;
    const done = await run(async () => {
      meetings.upsert(await meetingApi.mergeSpeakers(meeting.id, source, target));
      return true;
    });
    if (done) {
      editId = null;
      toast.success('已合并');
      onrefresh();
    }
  }

  async function unmerge(id: string) {
    const done = await run(async () => {
      meetings.upsert(await meetingApi.unmergeSpeaker(meeting.id, id));
      return true;
    });
    if (done) {
      toast.success(`已撤销，「${ownName(id)}」恢复为单独的说话人`);
      onrefresh();
    }
  }

  async function accept(list?: string[]) {
    const done = await run(async () => {
      meetings.upsert(await meetingApi.acceptGuesses(meeting.id, list));
      return true;
    });
    if (done) toast.success(list && list.length === 1 ? '已采纳' : '已全部采纳');
  }

  async function rerun() {
    const done = await run(async () => {
      meetings.upsert(await meetingApi.runOp(meeting.id, 'speakers'));
      return true;
    });
    if (done) toast.info('已开始重新识别，结果会作为建议显示，采纳后才生效');
  }
</script>

{#if ids.length}
  <section class="card p-4 sm:p-5">
    <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
      <h2 class="flex items-center gap-2 text-[14px] font-semibold">
        <Users class="size-4 text-accent" />说话人
        <span class="font-normal text-muted">{active.length} 位</span>
      </h2>
      <p class="text-[12px] text-muted">点名字可以改名或合并，改名后纪要和逐字稿一起更新</p>
      {#if meeting.status === 'done'}
        <button
          class="btn btn-ghost btn-sm ml-auto"
          disabled={!canRerun || busy}
          onclick={rerun}
          title="让大模型根据称呼和自我介绍重新猜每位说话人的名字"
        >
          {#if meeting.op === 'speakers'}
            <LoaderCircle class="size-3.5 animate-spin" /> 正在识别…
          {:else}
            <UserRoundPen class="size-3.5" /> 重新识别说话人
          {/if}
        </button>
      {/if}
    </div>

    <div class="mt-3 flex flex-wrap gap-2">
      {#each active as id (id)}
        {@const stat = stats.total.get(id)}
        <button
          class="inline-flex h-8 max-w-full items-center gap-2 rounded-full border border-line bg-surface pr-3 pl-2.5 text-[13px] transition-colors hover:border-line-strong hover:bg-surface-2 disabled:opacity-60"
          disabled={locked}
          onclick={() => openEdit(id)}
          title={locked ? '处理完成后可以改名' : '改名或合并'}
        >
          <span class="size-2.5 shrink-0 rounded-full {speakerTone(id)}" aria-hidden="true"></span>
          <span class="truncate font-medium">{speakerName(speakers, id)}</span>
          {#if stat}
            <span class="tabular shrink-0 text-[12px] text-muted">{stat.count} 句 · {spoken(stat.ms)}</span>
          {/if}
        </button>
      {/each}
    </div>

    {#if merged.length}
      <div class="mt-2.5 flex flex-wrap gap-2">
        {#each merged as id (id)}
          <span class="inline-flex h-7 max-w-full items-center gap-1.5 rounded-full bg-surface-2 pr-1 pl-2.5 text-[12px] text-muted">
            <span class="size-2 shrink-0 rounded-full opacity-50 {speakerTone(id)}" aria-hidden="true"></span>
            <span class="truncate">{ownName(id)} 已并入 {speakerName(speakers, id)}</span>
            <button
              class="h-5 shrink-0 rounded-full px-2 font-medium text-accent-ink hover:bg-accent-soft disabled:opacity-50"
              disabled={busy || locked}
              onclick={() => unmerge(id)}
            >
              撤销
            </button>
          </span>
        {/each}
      </div>
    {/if}

    {#if guesses.length && !locked}
      <div class="mt-4 rounded-xl bg-accent-soft/60 px-3 py-2.5">
        <div class="flex items-center gap-2 text-[12.5px] font-medium text-accent-ink">
          <Sparkles class="size-3.5 shrink-0" />
          <span class="min-w-0 flex-1">大模型根据称呼和自我介绍猜的名字，采纳后才生效</span>
          {#if guesses.length > 1}
            <button class="btn btn-secondary btn-sm shrink-0" disabled={busy} onclick={() => accept()}>全部采纳</button>
          {/if}
        </div>
        <ul class="mt-2 space-y-2">
          {#each guesses as id (id)}
            {@const guess = speakers[id].guess}
            {#if guess}
              <li class="flex items-start gap-2.5 text-[13px]">
                <span class="mt-[7px] size-2 shrink-0 rounded-full {speakerTone(id)}" aria-hidden="true"></span>
                <div class="min-w-0 flex-1">
                  <p>
                    <span class="text-ink-2">{speakerName(speakers, id)}</span>
                    <span class="text-muted">建议：</span><span class="font-semibold">{guess.name}</span>
                    <span class="text-[11.5px] text-muted">（{CONFIDENCE[guess.confidence] ?? guess.confidence}）</span>
                  </p>
                  {#if guess.evidence}
                    <p class="truncate text-[12px] text-muted" title={guess.evidence}>依据：“{guess.evidence}”</p>
                  {/if}
                </div>
                <button class="btn btn-secondary btn-sm shrink-0" disabled={busy} onclick={() => accept([id])}>采纳</button>
              </li>
            {/if}
          {/each}
        </ul>
      </div>
    {/if}

    {#if hints.length && !locked}
      <ul class="mt-3 space-y-2">
        {#each hints as id (id)}
          {@const hint = speakers[id].merge_hint}
          {#if hint}
            <li class="flex items-start gap-2.5 rounded-xl bg-warn-soft px-3 py-2 text-[13px]">
              <Merge class="mt-0.5 size-4 shrink-0 text-warn-ink" />
              <div class="min-w-0 flex-1">
                <p>
                  <span class="font-medium">{speakerName(speakers, id)}</span> 可能和
                  <span class="font-medium">{speakerName(speakers, hint.with)}</span> 是同一人
                </p>
                {#if hint.reason}<p class="mt-0.5 text-[12px] text-muted">{hint.reason}</p>{/if}
              </div>
              <button
                class="btn btn-secondary btn-sm shrink-0"
                disabled={busy}
                onclick={() => merge(id, resolveSpeaker(speakers, hint.with))}
              >
                合并
              </button>
            </li>
          {/if}
        {/each}
      </ul>
    {/if}
  </section>
{/if}

<Modal open={editing !== null} title="编辑说话人" size="sm" onclose={() => (editId = null)}>
  {#if editing}
    <form id="speaker-form" onsubmit={rename}>
      <label class="label" for="speaker-name">名字</label>
      <input
        id="speaker-name"
        class="field"
        maxlength={32}
        placeholder="说话人 {editing.id.replace(/^S/, '')}"
        bind:value={nameDraft}
        disabled={busy}
      />
      <p class="hint">纪要、逐字稿和导出里的名字会一起更新，不用重新生成纪要。留空恢复为默认名字。</p>
      {#if editGuess}
        <button
          type="button"
          class="mt-2.5 flex w-full items-start gap-2 rounded-lg bg-accent-soft px-3 py-2 text-left text-[12.5px] hover:bg-accent-soft/70"
          onclick={() => (nameDraft = editGuess.name)}
        >
          <Sparkles class="mt-0.5 size-3.5 shrink-0 text-accent-ink" />
          <span class="min-w-0">
            <span class="text-accent-ink">建议：<span class="font-semibold">{editGuess.name}</span>（{CONFIDENCE[editGuess.confidence] ??
                editGuess.confidence}），点击填入</span>
            {#if editGuess.evidence}<span class="mt-0.5 block text-muted">依据：“{editGuess.evidence}”</span>{/if}
          </span>
        </button>
      {/if}
    </form>

    {#if editing.stat}
      <p class="mt-4 flex flex-wrap items-center gap-x-2 text-[12.5px] text-muted">
        共 {editing.stat.count} 句，约 {spoken(editing.stat.ms)}
        {#if meeting.audio_available}
          <button
            class="inline-flex items-center gap-1 text-accent-ink hover:underline"
            onclick={() => onseek(editing.stat?.first ?? 0)}
          >
            <Play class="size-3" fill="currentColor" />听第一句（{clock(editing.stat.first)}）
          </button>
        {/if}
      </p>
    {/if}

    {#if mergeTargets.length}
      <div class="mt-4 border-t border-line pt-4">
        <label class="label" for="speaker-merge">合并到另一位说话人</label>
        <div class="flex gap-2">
          <select id="speaker-merge" class="field" bind:value={mergeTarget} disabled={busy}>
            {#each mergeTargets as id (id)}<option value={id}>{speakerName(speakers, id)}</option>{/each}
          </select>
          <button
            type="button"
            class="btn btn-secondary shrink-0"
            disabled={busy || !mergeTarget}
            onclick={() => editing && merge(editing.id, mergeTarget)}
          >
            <Merge class="size-4" /> 合并
          </button>
        </div>
        <p class="hint">同一个人被识别成了两位时使用，之后可以撤销。</p>
      </div>
    {/if}
  {/if}
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={() => (editId = null)}>取消</button>
    <button class="btn btn-primary" form="speaker-form" disabled={busy}>
      {#if busy}<LoaderCircle class="size-4 animate-spin" />{/if}保存名字
    </button>
  {/snippet}
</Modal>
