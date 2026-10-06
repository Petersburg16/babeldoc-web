// 会议大模型（后台的会议模型和整理方案）用到的常量与校验。
// 这里是 backend/app/meeting/llm_config.py 的手工镜像，改一边要同步另一边：
// STEPS、DEFAULT_TIMEOUTS、EFFORT_ORDER、EFFORT_LABELS 对应同名常量；
// RESERVED_PARAMS 对应 app/llm.py 的 RESERVED，PARAM_NAME 对应 _PARAM_NAME；
// paramError、stepError 对应 CustomParam、StepConfig 的校验。用途的中文名 STEP_LABELS 在 ./format.ts。
import { errorText } from '../format';
import { STEP_LABELS } from './format';
import type { EffortChoice, EffortLevel, LlmModelAdmin, LlmStep, LlmTestResult, StepConfig } from './types';

/** 思考强度从低到高 */
export const EFFORT_ORDER: EffortLevel[] = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
export const EFFORT_LABELS: Record<EffortLevel, string> = {
  none: '关闭',
  minimal: '极低',
  low: '低',
  medium: '中',
  high: '高',
  xhigh: '很高',
  max: '最高',
};

export const STEPS: LlmStep[] = ['speakers', 'polish', 'minutes', 'chat'];
const DEFAULT_TIMEOUTS: Record<LlmStep, number> = { speakers: 300, polish: 300, minutes: 900, chat: 600 };

/** 测试结果的一行摘要：成功时带回复，失败时带原因 */
export function testSummary(r: LlmTestResult) {
  const facts = [
    r.ok ? '连接正常' : r.error || '测试失败',
    r.latency_ms !== null ? `${(r.latency_ms / 1000).toFixed(1)} 秒` : '',
    r.ok || r.reasoning_tokens ? `思考 ${r.reasoning_tokens} token` : '',
    r.finish_reason ? `结束原因 ${r.finish_reason}` : '',
  ].filter(Boolean);
  return facts.join(' · ') + (r.ok ? ` · 回复：${r.reply || '（空）'}` : '');
}

export function failedTest(e: unknown): LlmTestResult {
  return {
    ok: false,
    latency_ms: null,
    reply: null,
    error: errorText(e),
    tokens: 0,
    reasoning_tokens: 0,
    finish_reason: null,
    sent: {},
  };
}

export function effortShort(choice: EffortChoice) {
  return choice === 'default' ? '默认' : EFFORT_LABELS[choice];
}

/** 新方案里一个用途的初始设置 */
export function newStep(step: LlmStep, modelId: number | null): StepConfig {
  return {
    model_id: modelId,
    effort: 'default',
    temperature: { on: false, value: 1 },
    top_p: { on: false, value: 1 },
    max_tokens: { on: false, value: 4096 },
    timeout_s: DEFAULT_TIMEOUTS[step],
    params: [],
  };
}

/** 打开了的参数开关、改过的超时和自定义参数，用于卡片和折叠时的摘要 */
export function stepFacts(sc: StepConfig, step: LlmStep): string[] {
  const facts: string[] = [];
  if (sc.temperature.on) facts.push(`温度 ${sc.temperature.value}`);
  if (sc.top_p.on) facts.push(`Top-P ${sc.top_p.value}`);
  if (sc.max_tokens.on) facts.push(`最大输出 ${sc.max_tokens.value}`);
  if (sc.timeout_s !== DEFAULT_TIMEOUTS[step]) facts.push(`超时 ${sc.timeout_s} 秒`);
  if (sc.params.length) facts.push(`自定义 ${sc.params.map((p) => p.name).join('、')}`);
  return facts;
}

const RESERVED_PARAMS = ['model', 'messages', 'stream', 'stream_options'];
const PARAM_NAME = /^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/;
const NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;

/** 和后端 CustomParam 的校验一致，空字符串表示没问题 */
export function paramError(sc: StepConfig, index: number): string {
  const p = sc.params[index];
  const name = p.name.trim();
  if (!name) return '请填写参数名';
  if (RESERVED_PARAMS.includes(name)) return `“${name}”由本站自己填写，不能作为自定义参数`;
  if (!PARAM_NAME.test(name)) return '参数名只能用字母、数字、下划线、点和连字符，且以字母或下划线开头';
  if (sc.params.findIndex((q) => q.name.trim() === name) !== index) return '参数名重复了';
  const toggled: Record<string, boolean> = {
    temperature: sc.temperature.on,
    top_p: sc.top_p.on,
    max_tokens: sc.max_tokens.on,
    max_completion_tokens: sc.max_tokens.on,
  };
  if (toggled[name]) return '和上面打开的开关重复了，请只保留一处';
  const raw = p.value.trim();
  if (p.type === 'number' && !NUMBER.test(raw)) return '不是数字';
  if (p.type === 'boolean' && raw !== 'true' && raw !== 'false') return '只能是 true 或 false';
  if (p.type === 'json') {
    try {
      JSON.parse(raw);
    } catch {
      return 'JSON 格式错误';
    }
  }
  return '';
}

const inRange = (v: unknown, min: number, max: number, integer = false) =>
  typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max && (!integer || Number.isInteger(v));

/** 保存前检查一个用途，返回给人看的错误（advanced 表示问题在高级参数里）；null 表示没问题 */
export function stepError(
  step: LlmStep,
  sc: StepConfig,
  model: LlmModelAdmin | undefined,
): { text: string; advanced: boolean } | null {
  const label = STEP_LABELS[step];
  if (!model) return { text: `请给“${label}”选一个模型`, advanced: false };
  if (sc.effort !== 'default' && !model.effort_levels.includes(sc.effort)) {
    return { text: `“${label}”的思考强度不在模型“${model.name}”的档位里，请重新选择`, advanced: false };
  }
  if (sc.temperature.on && !inRange(sc.temperature.value, 0, 2)) {
    return { text: `“${label}”的模型温度要在 0 到 2 之间`, advanced: true };
  }
  if (sc.top_p.on && !inRange(sc.top_p.value, 0, 1)) return { text: `“${label}”的 Top-P 要在 0 到 1 之间`, advanced: true };
  if (sc.max_tokens.on && !inRange(sc.max_tokens.value, 1, 1_000_000, true)) {
    return { text: `“${label}”的最大输出要是 1 到 1000000 之间的整数`, advanced: true };
  }
  if (!inRange(sc.timeout_s, 30, 7200, true)) return { text: `“${label}”的超时要是 30 到 7200 之间的整数（秒）`, advanced: true };
  for (let i = 0; i < sc.params.length; i++) {
    const problem = paramError(sc, i);
    if (problem) return { text: `“${label}”的自定义参数 ${sc.params[i].name.trim() || i + 1}：${problem}`, advanced: true };
  }
  return null;
}

/** 关着的开关不发送，但后端照样校验数值范围：不合法的换回默认值 */
export function cleanStep(sc: StepConfig): StepConfig {
  const keep = (on: boolean, value: number, ok: boolean, fallback: number) => ({ on, value: ok ? value : fallback });
  return {
    model_id: sc.model_id,
    effort: sc.effort,
    temperature: keep(sc.temperature.on, sc.temperature.value, inRange(sc.temperature.value, 0, 2), 1),
    top_p: keep(sc.top_p.on, sc.top_p.value, inRange(sc.top_p.value, 0, 1), 1),
    max_tokens: keep(sc.max_tokens.on, sc.max_tokens.value, inRange(sc.max_tokens.value, 1, 1_000_000, true), 4096),
    timeout_s: sc.timeout_s,
    params: sc.params.map((p) => ({ name: p.name.trim(), type: p.type, value: p.value })),
  };
}
