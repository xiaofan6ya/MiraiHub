/**
 * 智能体 · 本地偏好（模式 / 模型 / 思考 / 尺寸…），从 AgentPage 拆出（r3），逻辑逐字搬家。
 */
export type AgentMode = 'chat' | 'image' | 'video'
export type ThinkingPref = 'default' | 'on' | 'off'

export const AGENT_PREF_KEY = 'wb_agent_ui'

export interface AgentPrefs {
  mode: AgentMode
  model: string
  reasoning: string
  thinking: ThinkingPref
  imageSize: string
  videoSeconds: string
  videoResolution: string
  videoAspect: string
}

export function defaultAgentPrefs(): AgentPrefs {
  return {
    mode: 'chat',
    model: '',
    reasoning: '',
    thinking: 'default',
    imageSize: '1024x1024',
    videoSeconds: '5',
    videoResolution: '720P',
    videoAspect: '16:9',
  }
}

export function readAgentPrefs(): AgentPrefs {
  const fallback = defaultAgentPrefs()
  try {
    const raw = JSON.parse(localStorage.getItem(AGENT_PREF_KEY) || 'null') as Partial<AgentPrefs> | null
    if (!raw || typeof raw !== 'object') return fallback
    return {
      mode: raw.mode === 'image' || raw.mode === 'video' ? raw.mode : 'chat',
      model: typeof raw.model === 'string' ? raw.model : '',
      reasoning: typeof raw.reasoning === 'string' ? raw.reasoning : '',
      thinking: raw.thinking === 'on' || raw.thinking === 'off' ? raw.thinking : 'default',
      imageSize: typeof raw.imageSize === 'string' ? raw.imageSize : fallback.imageSize,
      videoSeconds: String(raw.videoSeconds || fallback.videoSeconds),
      videoResolution: typeof raw.videoResolution === 'string' ? raw.videoResolution : fallback.videoResolution,
      videoAspect: typeof raw.videoAspect === 'string' ? raw.videoAspect : fallback.videoAspect,
    }
  } catch {
    return fallback
  }
}

export function writeAgentPrefs(prefs: AgentPrefs): void {
  try {
    localStorage.setItem(AGENT_PREF_KEY, JSON.stringify(prefs))
  } catch {
    /* 隐私模式写不进去，不影响本次会话 */
  }
}

export function toAgentMode(value: unknown): AgentMode {
  return value === 'image' || value === 'video' ? value : 'chat'
}

export function toThinkingPref(value: unknown): ThinkingPref {
  return value === 'on' || value === 'off' ? value : 'default'
}
