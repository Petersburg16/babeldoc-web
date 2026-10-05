<script lang="ts">
  import { AudioWaveform, BookA, Bot, BrainCircuit, Gauge, ScrollText, Settings, Ticket, Users } from '../../lib/icons';
  import { router } from '../../lib/router.svelte';
  import AsrProviders from './AsrProviders.svelte';
  import Glossary from './Glossary.svelte';
  import Invites from './Invites.svelte';
  import Jobs from './Jobs.svelte';
  import MeetingLlm from './MeetingLlm.svelte';
  import Models from './Models.svelte';
  import Overview from './Overview.svelte';
  import SettingsPage from './Settings.svelte';
  import UsersPage from './Users.svelte';

  const tabs = [
    { path: '/admin', label: '概览', icon: Gauge, page: Overview },
    { path: '/admin/jobs', label: '任务', icon: ScrollText, page: Jobs },
    { path: '/admin/users', label: '用户', icon: Users, page: UsersPage },
    { path: '/admin/invites', label: '邀请码', icon: Ticket, page: Invites },
    { path: '/admin/models', label: '翻译模型', icon: Bot, page: Models },
    { path: '/admin/asr', label: '语音识别', icon: AudioWaveform, page: AsrProviders },
    { path: '/admin/meeting-llm', label: '会议模型', icon: BrainCircuit, page: MeetingLlm },
    { path: '/admin/glossary', label: '术语表', icon: BookA, page: Glossary },
    { path: '/admin/settings', label: '系统设置', icon: Settings, page: SettingsPage },
  ];

  const current = $derived(tabs.find((t) => t.path === router.path.replace(/\/$/, '')) ?? tabs[0]);
  const Page = $derived(current.page);
</script>

<div>
  <div class="mb-6">
    <h1 class="text-[22px] font-semibold tracking-tight">管理后台</h1>
    <nav class="-mx-1 mt-4 flex gap-1 overflow-x-auto border-b border-line px-1" aria-label="管理后台导航">
      {#each tabs as tab (tab.path)}
        <a
          href={tab.path}
          class="relative -mb-px flex h-10 shrink-0 items-center gap-1.5 border-b-2 px-3 text-[13.5px] font-medium transition-colors
            {current.path === tab.path ? 'border-accent text-ink' : 'border-transparent text-ink-2 hover:text-ink'}"
          aria-current={current.path === tab.path ? 'page' : undefined}
        >
          <tab.icon class="size-4" />
          {tab.label}
        </a>
      {/each}
    </nav>
  </div>
  <Page />
</div>
