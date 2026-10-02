<script lang="ts">
  import type { Snippet } from 'svelte';
  import { session } from '../lib/session.svelte';
  import Logo from './Logo.svelte';

  let { subtitle, children, below }: { subtitle: string; children: Snippet; below?: Snippet } = $props();
</script>

<div class="relative grid min-h-dvh place-items-center overflow-hidden px-4 py-12">
  <div aria-hidden="true" class="pointer-events-none absolute inset-0">
    <div class="absolute -top-48 left-1/2 h-[520px] w-[760px] -translate-x-1/2 rounded-full bg-accent/14 blur-3xl"></div>
    <div class="absolute -bottom-56 left-[12%] h-[380px] w-[480px] rounded-full bg-[#4a3aa7]/10 blur-3xl"></div>
  </div>
  <div class="relative w-full max-w-[390px]">
    <div class="mb-7 flex flex-col items-center text-center">
      <Logo class="size-12" />
      <h1 class="mt-4 text-[22px] font-semibold tracking-tight">{session.meta?.site_name ?? 'BabelDOC Web'}</h1>
      <p class="mt-1 text-[13.5px] text-muted">{subtitle}</p>
    </div>
    <div class="card p-6 shadow-pop sm:p-7">
      {@render children()}
    </div>
    {@render below?.()}
  </div>
</div>
