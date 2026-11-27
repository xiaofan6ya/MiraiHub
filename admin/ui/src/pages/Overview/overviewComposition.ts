import type { OverviewWindow } from '@/api/overview'

const count = (value: unknown): number => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0

/** Cache is a subset of prompt tokens, never a third segment added to the token total. */
export function overviewComposition(window: OverviewWindow | null | undefined) {
  const requests = count(window?.requests)
  const success = Math.min(requests, count(window?.ok_requests))
  const prompt = count(window?.prompt_tokens)
  const output = count(window?.completion_tokens)
  return {
    requests, success, failures: requests - success,
    successShare: requests ? success / requests : 0,
    prompt, output,
    promptShare: prompt + output ? prompt / (prompt + output) : 0,
    outputShare: prompt + output ? output / (prompt + output) : 0,
    hasTokens: prompt + output > 0,
  }
}
