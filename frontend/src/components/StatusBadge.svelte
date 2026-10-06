<script lang="ts">
  import { Ban, CircleCheck, CircleX, Clock, LoaderCircle } from '../lib/icons';
  import type { JobStatus } from '../lib/types';
  import Badge from './Badge.svelte';

  let { status }: { status: JobStatus } = $props();

  const variants = {
    queued: { label: '排队中', icon: Clock, cls: 'bg-surface-2 text-ink-2', spin: false },
    running: { label: '翻译中', icon: LoaderCircle, cls: 'bg-accent-soft text-accent-ink', spin: true },
    succeeded: { label: '已完成', icon: CircleCheck, cls: 'bg-good-soft text-good-ink', spin: false },
    failed: { label: '失败', icon: CircleX, cls: 'bg-bad-soft text-bad-ink', spin: false },
    canceled: { label: '已取消', icon: Ban, cls: 'bg-surface-2 text-muted', spin: false },
  } as const;

  const variant = $derived(variants[status]);
</script>

<Badge icon={variant.icon} label={variant.label} tone={variant.cls} spin={variant.spin} />
