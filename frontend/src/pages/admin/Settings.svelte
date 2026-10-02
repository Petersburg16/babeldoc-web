<script lang="ts">
  import { onMount } from 'svelte';
  import Segmented from '../../components/Segmented.svelte';
  import { api } from '../../lib/api';
  import { Gauge, Languages, LoaderCircle, Megaphone, Users } from '../../lib/icons';
  import { session } from '../../lib/session.svelte';
  import { toast } from '../../lib/toast.svelte';
  import type { SystemSettings } from '../../lib/types';

  let form = $state<SystemSettings | null>(null);
  let saved = $state('');
  let saving = $state(false);

  const dirty = $derived(form !== null && JSON.stringify(form) !== saved);
  const languages = $derived(session.meta?.languages ?? []);

  onMount(async () => {
    try {
      const data = await api.admin.settings();
      form = data;
      saved = JSON.stringify(data);
    } catch (e) {
      toast.error(e);
    }
  });

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (!form) return;
    saving = true;
    try {
      const data = await api.admin.saveSettings({
        ...form,
        max_concurrent_jobs: Number(form.max_concurrent_jobs),
        max_upload_mb: Number(form.max_upload_mb),
        max_pages_per_job: Number(form.max_pages_per_job),
        max_active_jobs_per_user: Number(form.max_active_jobs_per_user),
        default_page_quota: Number(form.default_page_quota),
        file_retention_days: Number(form.file_retention_days),
      });
      form = data;
      saved = JSON.stringify(data);
      await session.refreshMeta();
      toast.success('设置已保存');
    } catch (e) {
      toast.error(e);
    } finally {
      saving = false;
    }
  }
</script>

{#if !form}
  <div class="flex justify-center py-20"><LoaderCircle class="size-6 animate-spin text-muted" /></div>
{:else}
  <form class="space-y-5" onsubmit={save}>
    <section class="card p-5">
      <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><Megaphone class="size-4 text-accent" />站点</h2>
      <div class="mt-4 grid gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div>
          <label class="label" for="s-name">站点名称</label>
          <input id="s-name" class="field" required maxlength={40} bind:value={form.site_name} />
        </div>
        <div>
          <label class="label" for="s-ann">公告 <span class="font-normal text-muted">（显示在翻译页顶部，留空不显示）</span></label>
          <textarea id="s-ann" class="field" rows="2" maxlength={500} placeholder="例如：本站使用中转 API，请勿上传涉密文件" bind:value={form.announcement}></textarea>
        </div>
      </div>
    </section>

    <section class="card p-5">
      <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><Users class="size-4 text-accent" />注册与额度</h2>
      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <span class="label">注册方式</span>
          <Segmented
            bind:value={form.registration}
            ariaLabel="注册方式"
            options={[
              { value: 'invite', label: '邀请码' },
              { value: 'open', label: '开放注册' },
              { value: 'closed', label: '关闭' },
            ]}
          />
          <p class="hint">关闭后只能由管理员在“用户”页创建账号</p>
        </div>
        <div>
          <label class="label" for="s-quota">默认每月页数额度</label>
          <input id="s-quota" class="field" type="number" min="0" bind:value={form.default_page_quota} />
          <p class="hint">0 表示不限；可在“用户”页单独调整</p>
        </div>
        <div>
          <label class="label" for="s-active">每人同时排队/进行的任务数</label>
          <input id="s-active" class="field" type="number" min="1" max="50" bind:value={form.max_active_jobs_per_user} />
        </div>
        <div>
          <label class="label" for="s-retain">文件保留天数</label>
          <input id="s-retain" class="field" type="number" min="1" max="365" bind:value={form.file_retention_days} />
          <p class="hint">超过后自动删除原文与译文，任务记录保留</p>
        </div>
      </div>
    </section>

    <section class="card p-5">
      <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><Gauge class="size-4 text-accent" />任务处理</h2>
      <div class="mt-4 grid gap-4 sm:grid-cols-3">
        <div>
          <label class="label" for="s-conc">同时翻译的任务数</label>
          <input id="s-conc" class="field" type="number" min="1" max="4" bind:value={form.max_concurrent_jobs} />
          <p class="hint">4 核 6G 的服务器建议 1–2：每个任务约占 2 核、1.5 GB 内存</p>
        </div>
        <div>
          <label class="label" for="s-mb">单个文件大小上限（MB）</label>
          <input id="s-mb" class="field" type="number" min="1" max="200" bind:value={form.max_upload_mb} />
        </div>
        <div>
          <label class="label" for="s-pages">单个任务页数上限</label>
          <input id="s-pages" class="field" type="number" min="1" max="2000" bind:value={form.max_pages_per_job} />
          <p class="hint">超过 80 页会自动分段处理以节省内存</p>
        </div>
      </div>
    </section>

    <section class="card p-5">
      <h2 class="flex items-center gap-2 text-[14.5px] font-semibold"><Languages class="size-4 text-accent" />默认语言与水印</h2>
      <div class="mt-4 grid gap-4 sm:grid-cols-3">
        <div>
          <label class="label" for="s-in">默认原文语言</label>
          <select id="s-in" class="field" bind:value={form.default_lang_in}>
            {#each languages as lang (lang.code)}<option value={lang.code}>{lang.label}</option>{/each}
          </select>
        </div>
        <div>
          <label class="label" for="s-out">默认译文语言</label>
          <select id="s-out" class="field" bind:value={form.default_lang_out}>
            {#each languages as lang (lang.code)}<option value={lang.code}>{lang.label}</option>{/each}
          </select>
        </div>
        <div>
          <label class="label" for="s-wm">BabelDOC 水印</label>
          <select id="s-wm" class="field" bind:value={form.watermark_mode}>
            <option value="no_watermark">不加水印</option>
            <option value="watermarked">加水印</option>
            <option value="both">两种都输出（下载无水印版）</option>
          </select>
        </div>
      </div>
    </section>

    <div class="sticky bottom-4 flex justify-end">
      <div class="flex items-center gap-3 rounded-2xl border border-line bg-surface/90 px-3 py-2 shadow-pop backdrop-blur">
        <span class="text-[12.5px] text-muted">{dirty ? '有未保存的修改' : '所有修改已保存'}</span>
        <button class="btn btn-primary" disabled={!dirty || saving}>
          {#if saving}<LoaderCircle class="size-4 animate-spin" />{/if}保存设置
        </button>
      </div>
    </div>
  </form>
{/if}
