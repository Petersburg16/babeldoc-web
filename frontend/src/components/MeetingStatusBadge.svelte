<script lang="ts">
  import { Ban, CircleCheck, CircleX, Clock, LoaderCircle } from '../lib/icons';
  import { statusLabel } from '../lib/meeting/format';
  import type { MeetingStatus } from '../lib/meeting/types';
  import Badge from './Badge.svelte';

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
</script>

<Badge icon={variant.icon} label={statusLabel(status)} tone={variant.cls} spin={variant.spin} />
