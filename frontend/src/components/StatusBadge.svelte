<script lang="ts">
  import { Ban, CircleCheck, CircleX, Clock, LoaderCircle } from '../lib/icons';
  import type { JobStatus } from '../lib/types';

  let { status }: { status: JobStatus } = $props();

  const variants = {
    queued: { label: '排队中', icon: Clock, cls: 'bg-surface-2 text-ink-2', spin: false },
    running: { label: '翻译中', icon: LoaderCircle, cls: 'bg-accent-soft text-accent-ink', spin: true },
    succeeded: { label: '已完成', icon: CircleCheck, cls: 'bg-good-soft text-good-ink', spin: false },
    failed: { label: '失败', icon: CircleX, cls: 'bg-bad-soft text-bad-ink', spin: false },
    canceled: { label: '已取消', icon: Ban, cls: 'bg-surface-2 text-muted', spin: false },
  } as const;

  const variant = $derived(variants[status]);
  const Icon = $derived(variant.icon);
</script>

<span class="inline-flex h-6 items-center gap-1 rounded-full pr-2.5 pl-2 text-[12px] font-medium whitespace-nowrap {variant.cls}">
  <Icon class="size-3.5 {variant.spin ? 'animate-spin' : ''}" />
  {variant.label}
</span>
