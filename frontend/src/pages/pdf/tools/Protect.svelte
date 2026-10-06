<script lang="ts">
  import FilePicker from '../../../components/FilePicker.svelte';
  import Segmented from '../../../components/Segmented.svelte';
  import Switch from '../../../components/Switch.svelte';
  import { copyText } from '../../../lib/format';
  import { Copy, Eye } from '../../../lib/icons';
  import { pdfBlob, renamed, type Report } from '../../../lib/pdf/files';
  import { EyeOff } from '../../../lib/pdf/icons';
  import { readUserPdf } from '../../../lib/pdf/input';
  import {
    encryptPdf,
    isRestricted,
    limitLabels,
    passwordTooLong,
    type PrintLevel,
  } from '../../../lib/pdf/ops/security';
  import { toast } from '../../../lib/toast.svelte';
  import ToolFrame from '../ui/ToolFrame.svelte';

  let files = $state<File[]>([]);
  let userPassword = $state('');
  let confirmPassword = $state('');
  let ownerPassword = $state('');
  let showUser = $state(false);
  let showOwner = $state(false);
  let print = $state<PrintLevel>('full');
  let copy = $state(true);
  let modify = $state(true);
  let annotate = $state(true);
  let done = $state<{ owner: string; generated: boolean; limits: string[]; locked: boolean; replaced: boolean } | null>(null);

  const restricted = $derived(isRestricted({ print, copy, modify, annotate }));
  const mismatch = $derived(!!confirmPassword && confirmPassword !== userPassword);
  const problem = $derived.by(() => {
    if (!files.length) return '请先选择一个 PDF';
    if (!userPassword && !restricted) return '请设置打开密码，或至少限制一项权限';
    if (passwordTooLong(userPassword) || (restricted && passwordTooLong(ownerPassword))) return '密码太长，最多 127 字节（约 42 个汉字）';
    if (userPassword && confirmPassword !== userPassword) return mismatch ? '两次输入的打开密码不一致' : '请再输入一次打开密码';
    if (restricted && ownerPassword && ownerPassword === userPassword) return '权限密码要与打开密码不同，否则限制不起作用';
    return '';
  });

  async function run(report: Report) {
    done = null;
    const file = files[0];
    const permissions = { print, copy, modify, annotate };
    const user = userPassword;
    const owner = restricted ? ownerPassword : '';
    report(null, '读取文件');
    // 已加密的文件先解开（需要时弹窗要原密码），再按新设置加密
    const source = await readUserPdf(file);
    report(null, '正在加密');
    const out = await encryptPdf(source.bytes, { userPassword: user, ownerPassword: owner, permissions });
    done = {
      owner: out.ownerPassword,
      generated: out.generated,
      limits: limitLabels(permissions),
      locked: !!user,
      replaced: source.encrypted,
    };
    return [{ name: renamed(file.name, '已加密'), blob: pdfBlob(out.bytes) }];
  }

  async function copyOwner() {
    if (done && (await copyText(done.owner))) toast.success('已复制权限密码');
  }
</script>

{#snippet eye(shown: boolean, toggle: () => void)}
  <button
    type="button"
    class="btn btn-ghost btn-sm btn-icon absolute top-1/2 right-1 -translate-y-1/2"
    aria-label={shown ? '隐藏密码' : '显示密码'}
    aria-pressed={shown}
    onclick={toggle}
  >
    {#if shown}<EyeOff class="size-4" />{:else}<Eye class="size-4" />{/if}
  </button>
{/snippet}

<ToolFrame
  resetKey={files}
  runLabel="加密 PDF"
  canRun={!problem}
  blocked={problem}
  onrun={run}
  onreset={() => {
    files = [];
    userPassword = confirmPassword = ownerPassword = '';
    done = null;
  }}
>
  {#snippet input()}
    <FilePicker bind:files accept="application/pdf,.pdf" hint="已加密的文件会先请你输入原密码，再按新设置加密" />
  {/snippet}

  {#snippet options()}
    <div>
      <label class="label" for="protect-user">打开密码</label>
      <div class="relative">
        <input
          id="protect-user"
          class="field pr-10"
          type={showUser ? 'text' : 'password'}
          autocomplete="new-password"
          autocapitalize="off"
          spellcheck="false"
          bind:value={userPassword}
          oninput={(e) => {
            // 清空后确认框会隐藏，旧的确认值不能留到下次
            if (!e.currentTarget.value) confirmPassword = '';
          }}
        />
        {@render eye(showUser, () => (showUser = !showUser))}
      </div>
      {#if userPassword}
        <input
          id="protect-confirm"
          class="field mt-2"
          type={showUser ? 'text' : 'password'}
          autocomplete="new-password"
          autocapitalize="off"
          spellcheck="false"
          placeholder="再输入一次"
          aria-label="确认打开密码"
          aria-invalid={mismatch}
          bind:value={confirmPassword}
        />
        {#if mismatch}
          <p class="mt-1 text-[12px] text-bad-ink">两次输入不一致</p>
        {:else}
          <p class="hint">忘记后无法找回，请妥善保存</p>
        {/if}
      {:else}
        <p class="hint">留空则不设打开密码，只限制下面的权限</p>
      {/if}
    </div>

    <div>
      <p class="label">权限</p>
      <div class="flex items-center gap-3 py-1">
        <span class="shrink-0 text-[13.5px] text-ink">打印</span>
        <div class="min-w-0 flex-1">
          <Segmented
            size="sm"
            bind:value={print}
            ariaLabel="打印"
            options={[
              { value: 'full', label: '高质量' },
              { value: 'low', label: '低质量' },
              { value: 'none', label: '禁止' },
            ]}
          />
        </div>
      </div>
      <Switch bind:checked={copy} label="允许复制文字" />
      <Switch bind:checked={modify} label="允许修改" description="编辑内容，增删、旋转页面" />
      <Switch bind:checked={annotate} label="允许注释和填表" />
      <p class="hint">限制只在遵守规范的阅读器里生效</p>
    </div>

    {#if restricted}
      <div>
        <label class="label" for="protect-owner">权限密码<span class="font-normal text-muted">（可选）</span></label>
        <div class="relative">
          <input
            id="protect-owner"
            class="field pr-10"
            type={showOwner ? 'text' : 'password'}
            autocomplete="new-password"
            autocapitalize="off"
            spellcheck="false"
            bind:value={ownerPassword}
          />
          {@render eye(showOwner, () => (showOwner = !showOwner))}
        </div>
        <p class="hint">修改或解除限制时用，留空则自动生成</p>
      </div>
    {/if}
  {/snippet}

  {#snippet summary()}
    {#if done}
      <p>
        已用 AES-256 加密：{done.locked ? '打开时需要密码' : '不需要密码就能打开'}{done.limits.length
          ? `，禁止${done.limits.join('、')}`
          : ''}。{done.replaced ? '原文件的加密已换成新设置。' : ''}
      </p>
      {#if done.generated}
        <div class="mt-3 rounded-xl bg-surface-2 p-3">
          <p class="text-[12.5px] text-muted">自动生成的权限密码只显示这一次，以后修改或解除限制时要用：</p>
          <div class="mt-1.5 flex items-center gap-2">
            <code id="generated-owner-password" class="min-w-0 flex-1 font-mono text-[14px] font-semibold break-all text-ink">{done.owner}</code>
            <button class="btn btn-secondary btn-sm" onclick={copyOwner}><Copy class="size-3.5" /> 复制</button>
          </div>
        </div>
      {/if}
    {/if}
  {/snippet}
</ToolFrame>
