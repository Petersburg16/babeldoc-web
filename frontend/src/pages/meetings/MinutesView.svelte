<script lang="ts">
  // 纪要视图（由 F3 实现）
  import type { MeetingDetail } from '../../lib/meeting/types';

  let { meeting, onseek }: { meeting: MeetingDetail; onseek: (ms: number) => void } = $props();
</script>

<div data-meeting={meeting.id} data-seek={typeof onseek}></div>
