<script lang="ts">
  import Menu from '../../components/Menu.svelte';
  import Segmented from '../../components/Segmented.svelte';
  import { ChevronDown, Download, FileText } from '../../lib/icons';
  import { exportUrl } from '../../lib/meeting/api';
  import { FileType, Subtitles, TextQuote } from '../../lib/meeting/icons';
  import type { ExportContent, ExportFormat, MeetingDetail } from '../../lib/meeting/types';

  // 只在已有逐字稿时显示（MeetingDetailPage 外层判断），这里不再处理没有逐字稿的情况
  interface Props {
    meeting: MeetingDetail;
  }

  let { meeting }: Props = $props();

  const hasMinutes = $derived(!!meeting.minutes_md);

  let chosen = $state<ExportContent>('both');
  // 纪要还没生成时只能导出逐字稿
  const content = $derived<ExportContent>(hasMinutes ? chosen : 'transcript');

  const formats: { format: ExportFormat; label: string; hint: string; icon: typeof FileText }[] = [
    { format: 'docx', label: 'Word', hint: '.docx，适合打印和归档', icon: FileText },
    { format: 'md', label: 'Markdown', hint: '.md，保留标题和列表', icon: FileType },
    { format: 'txt', label: '纯文本', hint: '.txt，任何地方都能打开', icon: TextQuote },
  ];
</script>

<Menu width="w-64">
  {#snippet trigger({ toggle, open })}
    <button class="btn btn-secondary btn-sm" aria-expanded={open} aria-haspopup="menu" onclick={toggle}>
      <Download class="size-3.5" /> 导出 <ChevronDown class="size-3.5" />
    </button>
  {/snippet}
  {#snippet children({ close })}
    <div class="px-1.5 pt-1 pb-1.5">
      <p class="mb-1.5 text-[12px] text-muted">导出内容</p>
      <Segmented
        bind:value={chosen}
        size="sm"
        ariaLabel="导出内容"
        options={[
          { value: 'both', label: '全部', disabled: !hasMinutes },
          { value: 'minutes', label: '只要纪要', disabled: !hasMinutes },
          { value: 'transcript', label: '只要逐字稿' },
        ]}
      />
    </div>
    <div class="menu-sep"></div>
    {#each formats as item (item.format)}
      <a
        class="menu-item"
        role="menuitem"
        href={exportUrl(meeting.id, item.format, content)}
        download
        onclick={close}
      >
        <item.icon class="size-4 shrink-0 text-muted" />
        <span class="min-w-0 flex-1">
          <span class="block">{item.label}</span>
          <span class="block text-[11.5px] text-muted">{item.hint}</span>
        </span>
      </a>
    {/each}
    <div class="menu-sep"></div>
    <a
      class="menu-item"
      role="menuitem"
      href={exportUrl(meeting.id, 'srt', 'transcript')}
      download
      onclick={close}
    >
      <Subtitles class="size-4 shrink-0 text-muted" />
      <span class="min-w-0 flex-1">
        <span class="block">字幕</span>
        <span class="block text-[11.5px] text-muted">.srt，只含逐字稿，可配合录音播放</span>
      </span>
    </a>
  {/snippet}
</Menu>
