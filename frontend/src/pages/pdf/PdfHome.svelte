<script lang="ts">
  import { bytes } from '../../lib/format';
  import { engines } from '../../lib/pdf/engines.svelte';
  import { CATEGORIES, TOOLS } from '../../lib/pdf/tools';

  const groups = CATEGORIES.map((c) => ({ ...c, tools: TOOLS.filter((t) => t.category === c.id) }));

  $effect(() => {
    void engines.refresh();
  });
</script>

<div>
  <header class="mb-7">
    <h1 class="text-[22px] font-semibold tracking-tight">PDF 处理</h1>
    <p class="mt-1 text-[13.5px] text-ink-2">所有处理都在你的浏览器里完成，文件不会上传到服务器。</p>
  </header>

  <div class="space-y-8">
    {#each groups as group (group.id)}
      <section>
        <h2 class="mb-3 text-[13px] font-medium text-muted">{group.name}</h2>
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {#each group.tools as tool (tool.id)}
            {@const Icon = tool.icon}
            {@const pending = engines.pendingBytes(tool.engines)}
            <a
              href="/pdf/{tool.id}"
              class="card group flex items-start gap-3.5 p-4 transition-[border-color,box-shadow] hover:border-accent/50 hover:shadow-pop"
            >
              <div class="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent-ink transition-transform group-hover:-translate-y-0.5">
                <Icon class="size-5" strokeWidth={1.75} />
              </div>
              <div class="min-w-0 flex-1">
                <p class="text-[14.5px] font-medium">{tool.name}</p>
                <p class="mt-0.5 text-[12.5px] leading-snug text-muted">{tool.desc}</p>
                {#if pending >= 1_000_000}
                  <p class="mt-2 inline-flex rounded-md bg-surface-2 px-1.5 py-0.5 text-[11.5px] text-ink-2">
                    首次使用需下载约 {bytes(pending)}
                  </p>
                {/if}
              </div>
            </a>
          {/each}
        </div>
      </section>
    {/each}
  </div>

  <p class="mt-10 text-[12px] leading-relaxed text-muted">
    处理功能移植自 <a class="hover:text-ink-2 hover:underline" href="https://github.com/alam00000/bentopdf" target="_blank" rel="noopener"
      >BentoPDF</a
    >，按 AGPL-3.0 许可使用；本站修改后的<a
      class="hover:text-ink-2 hover:underline"
      href="https://github.com/Petersburg16/babeldoc-web"
      target="_blank"
      rel="noopener">完整源码</a
    >同样公开。
  </p>
</div>
