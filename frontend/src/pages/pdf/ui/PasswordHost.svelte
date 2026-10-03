<script lang="ts">
  import Modal from '../../../components/Modal.svelte';
  import { passwordState } from '../../../lib/pdf/password.svelte';

  const current = $derived(passwordState.current);
  let value = $state('');
  let input = $state<HTMLInputElement>();

  $effect(() => {
    if (current) {
      value = '';
      queueMicrotask(() => input?.focus());
    }
  });

  function submit(event: SubmitEvent) {
    event.preventDefault();
    if (value) passwordState.answer(value);
  }
</script>

<Modal
  open={!!current}
  title="需要打开密码"
  description={current ? `「${current.filename}」已加密` : ''}
  size="sm"
  onclose={() => passwordState.answer(null)}
>
  <form id="pdf-password" onsubmit={submit}>
    <label class="label" for="pdf-password-input">密码</label>
    <input
      id="pdf-password-input"
      bind:this={input}
      class="field"
      type="password"
      autocomplete="off"
      bind:value
      aria-invalid={current?.incorrect}
    />
    {#if current?.incorrect}
      <p class="mt-1.5 text-[12.5px] text-bad-ink">密码不正确，请重试</p>
    {:else}
      <p class="hint">密码只在你的浏览器里使用，不会上传</p>
    {/if}
  </form>
  {#snippet footer()}
    <button class="btn btn-secondary" onclick={() => passwordState.answer(null)}>取消</button>
    <button class="btn btn-primary" form="pdf-password" disabled={!value}>确定</button>
  {/snippet}
</Modal>
