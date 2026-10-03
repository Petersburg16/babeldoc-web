<script lang="ts">
  import { CircleAlert, CircleCheck, Eye, KeyRound, LoaderCircle, RefreshCw, Shield } from '../../../lib/icons';
  import { engines } from '../../../lib/pdf/engines.svelte';
  import { pdfBlob, readBytes, renamed, type Report } from '../../../lib/pdf/files';
  import { EyeOff, LockOpen } from '../../../lib/pdf/icons';
  import { Cancelled } from '../../../lib/pdf/input';
  import type { EncryptionState } from '../../../lib/pdf/ops/security';
  import FilePicker from '../ui/FilePicker.svelte';
  import ToolFrame from '../ui/ToolFrame.svelte';

  type Check =
    | { status: 'checking' }
    | { status: 'error'; title: string; message: string; retry: boolean }
    | ({ status: 'done' } & EncryptionState);

  let files = $state<File[]>([]);
  let check = $state<Check | null>(null);
  let attempt = $state(0);
  let password = $state('');
  let show = $state(false);
  let wrong = $state(false);
  let passwordInput = $state<HTMLInputElement | null>(null);

  // 分阶段给出错误：网络问题可以重试，文件问题只能换文件
  async function inspect(file: File): Promise<Check> {
    const failed = (title: string, message: string, retry = false): Check => ({ status: 'error', title, message, retry });
    let security: typeof import('../../../lib/pdf/ops/security');
    try {
      [security] = await Promise.all([import('../../../lib/pdf/ops/security'), engines.ensure(['qpdf'])]);
    } catch (e) {
      console.error(e);
      return failed('引擎下载失败', '可能是网络中断了，请检查网络后重试。', true);
    }
    let bytes: Uint8Array;
    try {
      bytes = await readBytes(file);
    } catch (e) {
      console.error(e);
      return failed('读取失败', '无法读取这个文件，它可能已被移动或删除，请重新选择。');
    }
    try {
      return { status: 'done', ...(await security.inspectEncryption(bytes)) };
    } catch (e) {
      console.error(e);
      // 退出码 -1 是 worker 本身出错，不是文件的问题
      const { QpdfError } = await import('../../../lib/pdf/engines/qpdf');
      if (e instanceof QpdfError && e.code === -1) return failed('处理引擎出错', '请重试，仍不行请刷新页面。', true);
      return failed('读取失败', '无法识别为 PDF，文件可能已损坏。');
    }
  }

  // 选好文件就检查加密状态，决定是要密码还是直接解除限制（qpdf 首次下载约 400 KB）
  $effect(() => {
    const file = files[0];
    void attempt; // 点“重试”时重新检查
    password = '';
    wrong = false;
    check = file ? { status: 'checking' } : null;
    if (!file) return;
    let alive = true;
    void inspect(file).then((result) => {
      if (alive) check = result;
    });
    return () => {
      alive = false;
    };
  });

  const info = $derived(check?.status === 'done' ? check : null);
  // open：加了密，但打开不要密码、也没禁止任何操作（不少软件默认这样加密）
  const mode = $derived(
    !info ? null : !info.encrypted ? 'plain' : info.needsPassword ? 'locked' : info.limits.length ? 'restricted' : 'open',
  );
  const blocked = $derived.by(() => {
    if (!files.length) return '请先选择一个 PDF';
    if (check?.status === 'checking') return '正在检查加密状态…';
    if (check?.status === 'error') return check.retry ? '请点上方的“重试”' : '请换一个文件';
    if (mode === 'plain') return '这个文件没有加密，不需要处理';
    if (mode === 'locked' && !password) return '请输入密码';
    return '';
  });
  const runLabel = $derived(mode === 'restricted' ? '解除限制' : mode === 'open' ? '去掉加密' : '解除密码');

  async function run(report: Report) {
    const [{ removeEncryption }, { QpdfError }] = await Promise.all([
      import('../../../lib/pdf/ops/security'),
      import('../../../lib/pdf/engines/qpdf'),
    ]);
    const file = files[0];
    const pw = mode === 'locked' ? password : undefined;
    wrong = false;
    report(null, pw ? '正在解密' : mode === 'open' ? '正在去掉加密' : '正在解除限制');
    try {
      const out = await removeEncryption(await readBytes(file), pw);
      return [{ name: renamed(file.name, '已解密'), blob: pdfBlob(out) }];
    } catch (e) {
      if (e instanceof QpdfError && e.message === '密码不正确') {
        wrong = true;
        passwordInput?.focus();
        passwordInput?.select();
        // 密码框下已经提示，不再让 ToolFrame 另弹一条错误（换文件、重新输入时也就不会残留）
        throw new Cancelled();
      }
      throw e;
    }
  }

  // 检查出需要密码时，焦点直接落到密码框
  const focusOnMount = (node: HTMLInputElement) => node.focus();
</script>

<ToolFrame engines={['qpdf']} {runLabel} canRun={!blocked} {blocked} onrun={run} onreset={() => (files = [])}>
  {#snippet input()}
    <FilePicker bind:files accept="application/pdf,.pdf" hint="选好后会自动检查：需要密码，还是只限制了打印、复制" />
  {/snippet}

  {#snippet options()}
    {#if !check}
      <p class="text-[13px] text-muted">选择文件后会自动检查加密状态。</p>
    {:else if check.status === 'checking'}
      <p class="flex items-center gap-2 text-[13px] text-muted" aria-live="polite">
        <LoaderCircle class="size-4 animate-spin" /> 正在检查加密状态…
      </p>
    {:else if check.status === 'error'}
      <div class="space-y-3" role="alert">
        {@render status(CircleAlert, 'bg-bad-soft text-bad-ink', check.title, check.message)}
        {#if check.retry}
          <button type="button" class="btn btn-secondary btn-sm" onclick={() => attempt++}>
            <RefreshCw class="size-3.5" /> 重试
          </button>
        {/if}
      </div>
    {:else if mode === 'plain'}
      {@render status(CircleCheck, 'bg-good-soft text-good-ink', '没有加密', '这个文件可以直接打开、打印和复制，不需要处理。')}
    {:else if mode === 'open' && info}
      <div class="space-y-3">
        {@render status(
          LockOpen,
          'bg-surface-2 text-ink-2',
          '已加密，但没有限制',
          `${info.method ? `${info.method} 加密，` : ''}打开不需要密码，也没有限制任何操作。`,
        )}
        <p class="text-[12.5px] leading-relaxed text-muted">去掉加密后内容不变，其他软件处理起来更省事；如有数字签名，会失效。</p>
      </div>
    {:else if mode === 'restricted' && info}
      <div class="space-y-3">
        {@render status(
          Shield,
          'bg-warn-soft text-warn-ink',
          '只限制了权限',
          `${info.method ? `${info.method} 加密，` : ''}打开不需要密码，但禁止了：`,
        )}
        <ul class="flex flex-wrap gap-1.5" aria-label="被禁止的操作">
          {#each info.limits as limit (limit)}
            <li class="rounded-md bg-surface-2 px-2 py-0.5 text-[12.5px] text-ink-2">{limit}</li>
          {/each}
        </ul>
        <p class="text-[12.5px] leading-relaxed text-muted">
          不用密码就能直接解除。请只用于你有权编辑的文件；如有数字签名，解除后会失效。
        </p>
      </div>
    {:else if mode === 'locked'}
      <div class="space-y-3">
        {@render status(KeyRound, 'bg-accent-soft text-accent-ink', '需要密码才能打开', '输入密码后会去掉密码和全部权限限制。')}
        <div>
          <label class="label" for="unlock-password">密码</label>
          <div class="relative">
            <input
              id="unlock-password"
              class="field pr-10"
              type={show ? 'text' : 'password'}
              autocomplete="off"
              autocapitalize="off"
              spellcheck="false"
              bind:value={password}
              bind:this={passwordInput}
              {@attach focusOnMount}
              oninput={() => (wrong = false)}
              aria-invalid={wrong}
              aria-describedby="unlock-password-note"
            />
            <button
              type="button"
              class="btn btn-ghost btn-sm btn-icon absolute top-1/2 right-1 -translate-y-1/2"
              aria-label={show ? '隐藏密码' : '显示密码'}
              aria-pressed={show}
              onclick={() => (show = !show)}
            >
              {#if show}<EyeOff class="size-4" />{:else}<Eye class="size-4" />{/if}
            </button>
          </div>
          {#if wrong}
            <p id="unlock-password-note" class="mt-1 text-[12px] text-bad-ink" role="alert">密码不正确，请检查大小写和输入法后重新输入</p>
          {:else}
            <p id="unlock-password-note" class="hint">打开密码或权限密码都可以，只在你的浏览器里使用</p>
          {/if}
        </div>
      </div>
    {/if}
  {/snippet}
</ToolFrame>

{#snippet status(Icon: typeof Shield, tone: string, title: string, text: string)}
  <div class="flex items-start gap-2.5">
    <div class="grid size-8 shrink-0 place-items-center rounded-lg {tone}"><Icon class="size-4" /></div>
    <div class="min-w-0">
      <p class="text-[13.5px] font-medium">{title}</p>
      <p class="mt-0.5 text-[12.5px] leading-snug text-muted">{text}</p>
    </div>
  </div>
{/snippet}
