<script lang="ts">
  import { confirmState } from '../lib/confirm.svelte';
  import Modal from './Modal.svelte';

  const current = $derived(confirmState.current);
</script>

<Modal open={!!current} title={current?.title ?? ''} size="sm" onclose={() => confirmState.answer(false)}>
  {#if current?.message}
    <p class="text-[13.5px] leading-relaxed text-ink-2">{current.message}</p>
  {/if}
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={() => confirmState.answer(false)}>取消</button>
    <button class="btn {current?.danger ? 'btn-danger' : 'btn-primary'}" onclick={() => confirmState.answer(true)}>
      {current?.confirmText ?? '确定'}
    </button>
  {/snippet}
</Modal>
