<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import { confirm } from '../../lib/confirm.svelte';
  import { copyText } from '../../lib/format';
  import { Bot, CircleAlert, Copy, LoaderCircle, RefreshCw, RotateCcw, Trash2 } from '../../lib/icons';
  import { meetingApi, streamChat } from '../../lib/meeting/api';
  import { fillSpeakers } from '../../lib/meeting/format';
  import { MessagesSquare, Send, Square } from '../../lib/meeting/icons';
  import type { ChatMessage, MeetingDetail } from '../../lib/meeting/types';
  import { toast } from '../../lib/toast.svelte';
  import MeetingMarkdown from './MeetingMarkdown.svelte';

  let { meeting, onseek }: { meeting: MeetingDetail; onseek: (ms: number) => void } = $props();

  interface Entry {
    /** 列表的稳定键：服务器存好后也不换，免得整条重新渲染 */
    key: string;
    id?: number;
    role: 'user' | 'assistant' | 'error';
    content: string;
    streaming?: boolean;
    /** 用户点了停止或中途出错：这段回答只留在本页，服务器没有保存 */
    partial?: boolean;
    /** 问题还没被服务器存下时出错，可以原样重新发送 */
    retry?: string;
  }

  const SUGGESTIONS = [
    '这次会议做了哪些决定？',
    '每个人的发言要点是什么？',
    '有哪些待办，负责人是谁？',
    '有哪些问题还没有结论？',
  ];
  const MAX_CHARS = 2000;

  let entries = $state<Entry[]>([]);
  let loading = $state(true);
  let loadError = $state('');
  let input = $state('');
  let sending = $state(false);
  let clearing = $state(false);
  let list = $state<HTMLElement>();
  let textarea = $state<HTMLTextAreaElement>();
  let controller: AbortController | null = null;
  let seq = 0;
  // 用户往上翻看历史时，流式输出不把他拽回底部
  let stick = true;

  const hasTranscript = $derived(meeting.transcript_state !== 'none');
  const canSend = $derived(hasTranscript && !loading && !sending && !!input.trim());

  onMount(load);
  onDestroy(() => controller?.abort());

  // 新消息、流式追加文字时滚到底部
  $effect(() => {
    void entries.length;
    void entries.at(-1)?.content;
    if (stick) void tick().then(() => list && (list.scrollTop = list.scrollHeight));
  });

  // 输入框随内容长高，最多约 6 行
  $effect(() => {
    void input;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
  });

  function fromMessage(m: ChatMessage): Entry {
    return { key: `m${m.id}`, id: m.id, role: m.role, content: m.content };
  }

  async function load() {
    loading = true;
    loadError = '';
    try {
      entries = (await meetingApi.messages(meeting.id)).map(fromMessage);
      stick = true;
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  function onscroll() {
    if (list) stick = list.scrollHeight - list.scrollTop - list.clientHeight < 64;
  }

  function find(key: string) {
    return entries.find((e) => e.key === key);
  }

  async function send(text?: string) {
    const content = (text ?? input).trim().slice(0, MAX_CHARS);
    if (!content || sending || loading || !hasTranscript) return;
    if (text === undefined) input = '';
    const userKey = `local-${++seq}`;
    const answerKey = `local-${++seq}`;
    entries.push({ key: userKey, role: 'user', content }, { key: answerKey, role: 'assistant', content: '', streaming: true });
    stick = true;
    sending = true;
    const job = new AbortController();
    controller = job;
    let saved = false;
    let finished = false;

    await streamChat(
      meeting.id,
      content,
      {
        onStart: (message) => {
          saved = true;
          const entry = find(userKey);
          if (entry && message) Object.assign(entry, { id: message.id, content: message.content });
        },
        onDelta: (delta) => {
          const entry = find(answerKey);
          if (entry) entry.content += delta;
        },
        onDone: (message) => {
          finished = true;
          const entry = find(answerKey);
          if (entry) Object.assign(entry, { id: message.id, content: message.content, streaming: false });
        },
        onError: (message) => {
          finished = true;
          settleAnswer(answerKey);
          entries.push({ key: `local-${++seq}`, role: 'error', content: message, retry: saved ? undefined : content });
        },
      },
      job.signal,
    );

    if (!finished) {
      settleAnswer(answerKey);
      // 不是用户点的停止，却既没有 done 也没有 error：连接被中途断开了
      if (!job.signal.aborted) {
        entries.push({ key: `local-${++seq}`, role: 'error', content: '连接中断，回答不完整，请重新提问' });
      }
    }
    if (controller === job) controller = null;
    sending = false;
  }

  /** 回答没有正常结束：有内容就留着并标成“未保存”，没内容就去掉 */
  function settleAnswer(key: string) {
    const entry = find(key);
    if (!entry) return;
    if (entry.content.trim()) Object.assign(entry, { streaming: false, partial: true });
    else entries = entries.filter((e) => e.key !== key);
  }

  function stop() {
    controller?.abort();
  }

  function retry(entry: Entry) {
    const question = entry.retry;
    if (!question || sending) return;
    // 去掉出错的这条和它前面那条没存下来的问题，再原样发一次
    const index = entries.indexOf(entry);
    const before = entries[index - 1];
    entries = entries.filter((e) => e !== entry && !(e === before && e.role === 'user' && e.id === undefined));
    void send(question);
  }

  async function clear() {
    if (sending || clearing || !entries.length) return;
    const ok = await confirm({
      title: '清空对话？',
      message: '这场会议的全部问答记录都会删除，无法恢复。逐字稿和纪要不受影响。',
      confirmText: '清空',
      danger: true,
    });
    if (!ok) return;
    clearing = true;
    try {
      await meetingApi.clearMessages(meeting.id);
      entries = [];
    } catch (e) {
      toast.error(e);
    } finally {
      clearing = false;
    }
  }

  async function copy(entry: Entry) {
    if (await copyText(fillSpeakers(entry.content, meeting.speakers))) toast.success('已复制');
    else toast.error('复制失败，请手动选择文字复制');
  }

  function onkeydown(event: KeyboardEvent) {
    // 输入法组字时的回车是在确认候选词；Safari 先结束组字再发这个回车，只能靠 keyCode 229 认出来
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    void send();
  }

  function ask(question: string) {
    void send(question);
    textarea?.focus();
  }
</script>

<section class="card flex h-[min(72dvh,46rem)] min-h-[26rem] flex-col overflow-hidden">
  <header class="flex items-center gap-2 border-b border-line px-4 py-2.5">
    <MessagesSquare class="size-4 shrink-0 text-accent" />
    <h2 class="text-[13.5px] font-semibold">对话</h2>
    <span class="hidden min-w-0 truncate text-[12px] text-muted sm:inline">根据逐字稿和纪要回答，时间可以点击跳到录音</span>
    <button
      class="btn btn-ghost btn-sm ml-auto"
      disabled={sending || clearing || !entries.length}
      onclick={clear}
      title="删除这场会议的全部问答记录"
    >
      {#if clearing}<LoaderCircle class="size-3.5 animate-spin" />{:else}<Trash2 class="size-3.5" />{/if} 清空对话
    </button>
  </header>

  <div class="min-h-0 flex-1 overflow-y-auto px-4 py-4" bind:this={list} {onscroll}>
    {#if loading}
      <div class="grid h-full place-items-center text-[13px] text-muted">
        <span class="flex items-center gap-2"><LoaderCircle class="size-4 animate-spin" />正在加载对话记录…</span>
      </div>
    {:else if loadError}
      <div class="grid h-full place-items-center">
        <div class="flex flex-col items-center gap-3 text-center">
          <p class="flex items-center gap-1.5 text-[13px] text-bad-ink"><CircleAlert class="size-4" />{loadError}</p>
          <button class="btn btn-secondary btn-sm" onclick={load}><RefreshCw class="size-3.5" /> 重新加载</button>
        </div>
      </div>
    {:else if !entries.length}
      <div class="flex h-full flex-col items-center justify-center text-center">
        <div class="grid size-11 place-items-center rounded-2xl bg-accent-soft text-accent">
          <MessagesSquare class="size-5" strokeWidth={1.75} />
        </div>
        {#if hasTranscript}
          <p class="mt-3 text-[14px] font-medium">问问这场会议</p>
          <p class="mt-1 max-w-sm text-[12.5px] text-muted">回答会标出原话的时间，点击就能跳到录音对应的位置。</p>
          <div class="mt-5 grid w-full max-w-lg gap-2 sm:grid-cols-2">
            {#each SUGGESTIONS as question (question)}
              <button
                class="rounded-xl border border-line bg-surface px-3.5 py-2.5 text-left text-[13px] text-ink-2 transition-colors hover:border-line-strong hover:bg-surface-2 hover:text-ink"
                onclick={() => ask(question)}
              >
                {question}
              </button>
            {/each}
          </div>
        {:else}
          <p class="mt-3 text-[14px] font-medium">还不能提问</p>
          <p class="mt-1 max-w-sm text-[12.5px] text-muted">会议还没有逐字稿，识别完成后就可以针对会议内容提问。</p>
        {/if}
      </div>
    {:else}
      <div class="space-y-5">
        {#each entries as entry (entry.key)}
          {#if entry.role === 'user'}
            <div class="flex justify-end pl-8 sm:pl-16">
              <div class="rounded-2xl rounded-br-md bg-accent px-3.5 py-2 text-[14px] leading-relaxed break-words whitespace-pre-wrap text-white">
                {entry.content}
              </div>
            </div>
          {:else if entry.role === 'assistant'}
            <div class="group flex gap-2.5 pr-2 sm:pr-8">
              <div class="grid size-7 shrink-0 place-items-center rounded-full bg-accent-soft text-accent">
                <Bot class="size-4" />
              </div>
              <div class="min-w-0 flex-1 pt-0.5">
                {#if entry.content}
                  <MeetingMarkdown
                    source={entry.content}
                    speakers={meeting.speakers}
                    durationMs={meeting.duration_ms}
                    {onseek}
                  />
                {/if}
                {#if entry.streaming && !entry.content}
                  <p class="flex items-center gap-2 text-[13px] text-muted">
                    <LoaderCircle class="size-3.5 animate-spin" />正在阅读会议内容…
                  </p>
                {:else if entry.partial}
                  <p class="mt-1.5 text-[12px] text-muted">回答没有完成，这段内容不会保存</p>
                {:else if !entry.streaming}
                  <div class="mt-1 flex opacity-100 transition-opacity sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
                    <button class="btn btn-ghost btn-sm -ml-2 h-7 text-muted" onclick={() => copy(entry)}>
                      <Copy class="size-3.5" /> 复制
                    </button>
                  </div>
                {/if}
              </div>
            </div>
          {:else}
            <div class="flex items-start gap-2 rounded-xl bg-bad-soft px-3 py-2.5 text-[13px] leading-relaxed text-bad-ink">
              <CircleAlert class="mt-0.5 size-4 shrink-0" />
              <p class="min-w-0 flex-1 break-words">{entry.content}</p>
              {#if entry.retry}
                <button class="btn btn-ghost btn-sm -my-1 shrink-0 text-bad-ink" disabled={sending} onclick={() => retry(entry)}>
                  <RotateCcw class="size-3.5" /> 重新发送
                </button>
              {/if}
            </div>
          {/if}
        {/each}
      </div>
    {/if}
  </div>

  <footer class="border-t border-line p-3">
    <div
      class="flex items-end gap-2 rounded-xl border border-line-strong py-1.5 pr-1.5 pl-3 transition-[border-color,box-shadow] focus-within:border-accent focus-within:shadow-[0_0_0_3px_var(--accent-soft)] {hasTranscript
        ? 'bg-surface'
        : 'bg-surface-2'}"
    >
      <textarea
        bind:this={textarea}
        bind:value={input}
        class="max-h-40 min-h-7 flex-1 resize-none self-center bg-transparent py-0.5 text-[14px] leading-6 outline-none placeholder:text-muted disabled:cursor-not-allowed"
        rows="1"
        maxlength={MAX_CHARS}
        placeholder={hasTranscript ? '问点什么，例如：张老师对实验方案提了哪些意见？' : '识别完成后才能提问'}
        aria-label="向会议提问"
        disabled={!hasTranscript}
        {onkeydown}
      ></textarea>
      {#if sending}
        <button class="btn btn-secondary btn-sm shrink-0" onclick={stop} title="停止生成">
          <Square class="size-3 fill-current" /> 停止
        </button>
      {:else}
        <button class="btn btn-primary btn-sm btn-icon shrink-0" disabled={!canSend} onclick={() => send()} aria-label="发送" title="发送">
          <Send class="size-3.5" />
        </button>
      {/if}
    </div>
    <p class="mt-1.5 hidden px-1 text-[11.5px] text-muted sm:block">
      <span class="kbd">Enter</span> 发送，<span class="kbd">Shift</span> + <span class="kbd">Enter</span> 换行。回答由大模型根据会议内容生成，重要信息请回到原音核对。
    </p>
  </footer>
</section>
