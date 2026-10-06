<script lang="ts">
  /**
   * 密钥输入框（翻译模型、会议模型、识别服务共用）：已保存时用打码后的值作占位提示，留空保持不变；
   * “清空”把已保存的密钥标记为保存时删除，同时禁用并清空输入框，免得新输入的密钥和“清空”一起发出去
   */
  interface Props {
    id: string;
    value: string;
    /** 保存时删除已保存的密钥 */
    clear?: boolean;
    /** 已保存密钥的打码值；空字符串表示没有，不显示“清空”按钮 */
    saved: string;
    /** 没有已保存的密钥时的占位提示 */
    placeholder: string;
    required?: boolean;
  }

  let { id, value = $bindable(), clear = $bindable(), saved, placeholder, required = false }: Props = $props();

  function toggle() {
    clear = !clear;
    if (clear) value = '';
  }
</script>

<div class="flex gap-2">
  <input
    {id}
    class="field font-mono"
    type="password"
    autocomplete="off"
    spellcheck="false"
    {required}
    disabled={clear}
    placeholder={clear ? '保存后清空' : saved ? `已保存 ${saved}，留空保持不变` : placeholder}
    bind:value
  />
  {#if saved}
    <button type="button" class="btn btn-secondary shrink-0" onclick={toggle}>{clear ? '撤销' : '清空'}</button>
  {/if}
</div>
