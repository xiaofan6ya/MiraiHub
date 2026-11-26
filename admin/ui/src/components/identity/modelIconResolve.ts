/**
 * 模型厂商图标 key 解析：严格照抄旧版 console-app.js 的 getModelIcon() 匹配顺序。
 *   1) 精确命中 vendor
 *   2) 模式 id（fast / balanced / primary / deep）精确命中
 *   3) 别名表归一 + 子串包含匹配（跳过模式 id）
 *   4) 兜底 default
 */
import { MODEL_ICONS } from './modelIcons'

export const MODEL_MODES = ['fast', 'balanced', 'primary', 'deep']

/** 模型名里的别名 → 图标 key（上游/Panel 写法五花八门，先抹平差异）。 */
export const VENDOR_ALIASES: Array<[RegExp, string]> = [
  [/(^|[^a-z])gpt|o1|o3|chatgpt/, 'openai'],
  [/gemini|bard/, 'gemini'],
  [/grok|xai/, 'grok'],
  [/qwen|qwq|tongyi|通义/, 'qwen'],
  [/doubao|seed[_-]?m|volc|bytedance|豆包/, 'doubao'],
  [/kling|可灵/, 'kling'],
  [/step[_-]?\d|stepfun|阶跃/, 'stepfun'],
  [/glm|chatglm|zhipu/, 'glm'],
  [/kimi|moonshot/, 'kimi'],
  [/deepseek/, 'deepseek'],
  [/claude|anthropic/, 'claude'],
  [/minimax|abab|mimo/, 'minimax'],
  [/hunyuan|hy\d|tencent|混元/, 'hunyuan'],
]

export function resolveModelIconKey(
  vendor?: string | null,
  icons: Record<string, unknown> = MODEL_ICONS,
): string | null {
  const has = (key: string): boolean => Object.prototype.hasOwnProperty.call(icons, key)
  const value = String(vendor || '').toLowerCase()
  if (value && has(value)) return value
  const mode = MODEL_MODES.find((item) => value === item)
  if (mode && has(mode)) return mode
  if (value) {
    for (const [pattern, key] of VENDOR_ALIASES) {
      if (pattern.test(value) && has(key)) return key
    }
    const normalized = value.includes('gpt') ? 'openai' : value
    const hit = Object.keys(icons).find(
      (key) => !MODEL_MODES.includes(key) && normalized.includes(key),
    )
    if (hit) return hit
  }
  return has('default') ? 'default' : null
}
