<script lang="ts">
  import { Ban, CircleCheck, CircleX, Clock, LoaderCircle } from '../lib/icons';
  import { statusLabel } from '../lib/meeting/format';
  import type { MeetingStatus } from '../lib/meeting/types';

  let { status }: { status: MeetingStatus } = $props();

  const working = { icon: LoaderCircle, cls: 'bg-accent-soft text-accent-ink', spin: true };
  const variants = {
    uploading: working,
    queued: { icon: Clock, cls: 'bg-surface-2 text-ink-2', spin: false },
    transcoding: working,
    transcribing: working,
    processing: working,
    done: { icon: CircleCheck, cls: 'bg-good-soft text-good-ink', spin: false },
    failed: { icon: CircleX, cls: 'bg-bad-soft text-bad-ink', spin: false },
    canceled: { icon: Ban, cls: 'bg-surface-2 text-muted', spin: false },
  } as const;

  const variant = $derived(variants[status] ?? variants.queued);
  const Icon = $derived(variant.icon);
</script>

<span class="inline-flex h-6 items-center gap-1 rounded-full pr-2.5 pl-2 text-[12px] font-medium whitespace-nowrap {variant.cls}">
  <Icon class="size-3.5 {variant.spin ? 'animate-spin' : ''}" />
  {statusLabel(status)}
</span>
