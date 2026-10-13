import type { Dispatch, Ledger, Overrides, Phase } from '../types'

export const COOLDOWN_MS = 900_000
export const FORK_RACE_MS = 60_000
export const INERT_KEPT_RATIO = 0.8
export const FLOOR_STEP = 5
export const AUTO_RESUME_GAP_MS = 180_000
export const LINE_CAP = 500

export const GENERIC_RESUME =
  'Continue working on the objectives from before the compaction. Pick up at the next unfinished step and keep going.'

export const FALLBACK_FOCUS =
  'Preserve: the ticket and its spec, every ticket still open, absolute paths of files changed this session, decisions taken and why, and any correction the owner made. Continue at the first open ticket.'

const DISPATCHED_KINDS = ['subagent', 'workflow', 'agent']
const IN_FLIGHT = ['running', 'pending']
const PROMPT_MODE_PREFIXES = ['!', '/', '#', '@']

export type AllSessions = { isArmed: boolean; floor: number; ceiling: number; holdMs: number }

export type Effective = {
  isOn: boolean
  isOnInherited: boolean
  floor: number
  isFloorInherited: boolean
}

export type WaitReason = 'not-a-finished-turn' | 'off' | 'plan-mode' | 'no-reading' | 'below-floor' | 'inert' | 'busy' | 'cooldown'

export type AutoVerdict =
  | { act: 'fire'; releasedBy: 'none' | 'ceiling' | 'hold-spent' }
  | { act: 'hold'; heldSince: number }
  | { act: 'wait'; why: WaitReason }

export type AutoInput = {
  now: number
  reason: string
  effective: Effective
  all: AllSessions
  fill: number | null
  isPlanMode: boolean
  dispatched: readonly Dispatch[]
  phase: Phase
  ledger: Ledger
}

export type Command =
  | { kind: 'open' }
  | { kind: 'floor'; scope: 'here' | 'all'; value: number }
  | { kind: 'switch'; scope: 'here' | 'all'; isOn: boolean }
  | { kind: 'reset' }
  | { kind: 'now' }

export const USAGE =
  'Use /compaction, /compaction <1-100>, /compaction all <1-100>, /compaction on|off, /compaction all on|off, /compaction reset, or /compaction fire.'

export type TaskLike = { id: string; type: string; status: string; description: string }

export function isDispatched(task: TaskLike): boolean {
  const kind = task.type.toLowerCase()
  if (!kind) return true
  if (kind.includes('teammate')) return false
  return DISPATCHED_KINDS.includes(kind) || kind.endsWith('_agent')
}

export function inFlightDispatches(tasks: readonly TaskLike[]): Dispatch[] {
  return tasks
    .filter(task => IN_FLIGHT.includes(task.status) && isDispatched(task))
    .map(task => ({ id: task.id, type: task.type, description: oneLine(task.description, 160) }))
}

export function clampFloor(value: number): number {
  return Math.min(100, Math.max(1, Math.round(value)))
}

export function effective(overrides: Overrides, all: AllSessions): Effective {
  return {
    isOn: overrides.enabled ?? all.isArmed,
    isOnInherited: overrides.enabled === null,
    floor: overrides.floor ?? all.floor,
    isFloorInherited: overrides.floor === null,
  }
}

export function decideAuto(input: AutoInput): AutoVerdict {
  const { now, effective: here, all, fill, ledger } = input
  if (input.reason !== 'answer' && input.reason !== 'error') return { act: 'wait', why: 'not-a-finished-turn' }
  if (!here.isOn) return { act: 'wait', why: 'off' }
  if (input.isPlanMode) return { act: 'wait', why: 'plan-mode' }
  if (fill === null) return { act: 'wait', why: 'no-reading' }
  if (fill < here.floor) return { act: 'wait', why: 'below-floor' }
  if (ledger.isInert) return { act: 'wait', why: 'inert' }
  if (input.phase !== 'idle') return { act: 'wait', why: 'busy' }
  if (ledger.lastAttemptAt !== null && now - ledger.lastAttemptAt < COOLDOWN_MS) return { act: 'wait', why: 'cooldown' }
  if (input.dispatched.length === 0) return { act: 'fire', releasedBy: 'none' }
  if (fill >= all.ceiling) return { act: 'fire', releasedBy: 'ceiling' }
  const heldSince = ledger.heldSince ?? now
  if (now - heldSince >= all.holdMs) return { act: 'fire', releasedBy: 'hold-spent' }
  return { act: 'hold', heldSince }
}

export function parseCommand(args: string): Command | { usage: string } {
  const words = args.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const scope: 'here' | 'all' = words[0] === 'all' ? 'all' : 'here'
  const rest = scope === 'all' ? words.slice(1) : words
  if (words.length === 0) return { kind: 'open' }
  if (rest.length !== 1) return { usage: USAGE }
  const [word = ''] = rest
  if (word === 'on' || word === 'off') return { kind: 'switch', scope, isOn: word === 'on' }
  if (scope === 'here' && word === 'reset') return { kind: 'reset' }
  if (scope === 'here' && (word === 'fire' || word === 'now')) return { kind: 'now' }
  const number = Number(word.replace(/%$/, ''))
  if (Number.isInteger(number) && number >= 1 && number <= 100) return { kind: 'floor', scope, value: number }
  return { usage: USAGE }
}

const FENCED = /```[\s\S]*?(?:```|$)/g
const INLINE_CODE = /`[^`\n]*`/g
const LINK_OR_URL = /\[[^\]\n]*\]\([^)\n]*\)|https?:\/\/\S+/g
const EMPHASIS_ONLY = /^([*_]{1,3}).+\1$/
const QUESTION = /\?(?=[*_"'”’)\]]*(?:\s|$))/
const NOT_PROSE = ['#', '|', '>']

export function closesOnQuestion(text: string): boolean {
  const prose = text.replace(FENCED, '').replace(INLINE_CODE, '').replace(LINK_OR_URL, '')
  return prose
    .split('\n')
    .map(line => line.trim())
    .filter(line => line && !NOT_PROSE.some(mark => line.startsWith(mark)) && !EMPHASIS_ONLY.test(line))
    .some(line => QUESTION.test(line))
}

export function oneLine(text: string, cap: number): string {
  return text.split(/\s+/).filter(Boolean).join(' ').slice(0, cap)
}

export function defusePrefix(text: string): string {
  return PROMPT_MODE_PREFIXES.some(mark => text.startsWith(mark)) ? `Continue: ${text}` : text
}

export function focusPrompt(dispatched: readonly Dispatch[]): string {
  const pending = dispatched.length
    ? `Dispatched work still running, each with what it was started for: ${dispatched.map(task => `${task.type} "${task.description}"`).join('; ')}.`
    : 'No dispatched work is running.'
  return [
    'This conversation is about to be compacted. Write the two lines that carry it across.',
    'Answer with JSON only: {"focus": "<one line>", "resume": "<one line>"}.',
    '"focus" steers the summary: what to preserve. Where dispatched work is still running, the focus MUST name what each task was started for and what its result feeds, because that reason is re-read when the task completes and the summary cannot reconstruct it.',
    '"resume" is sent AFTER the compaction as the next prompt, so it must read as an instruction starting with an imperative verb, such as "Re-read X, then continue." It names what to re-read and re-verify before acting: the files the work last touched and the branch\'s own diff. It never points at a message above or the closing message, which the compaction deletes. It never names the next action and never restates the focus.',
    pending,
  ].join('\n')
}

export function parseFocus(text: string): { focus: string; resume: string } | null {
  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start < 0 || end <= start) return null
  try {
    const answer: unknown = JSON.parse(text.slice(start, end + 1))
    if (typeof answer !== 'object' || answer === null) return null
    const fields = answer as Record<string, unknown>
    const focus = typeof fields.focus === 'string' ? oneLine(fields.focus, LINE_CAP) : ''
    const resume = typeof fields.resume === 'string' ? defusePrefix(oneLine(fields.resume, LINE_CAP)) : ''
    return focus ? { focus, resume } : null
  } catch {
    return null
  }
}

export function fallbackFocus(dispatched: readonly Dispatch[]): string {
  if (!dispatched.length) return FALLBACK_FOCUS
  return oneLine(`${FALLBACK_FOCUS} Still running: ${dispatched.map(task => `${task.type} "${task.description}"`).join('; ')}.`, LINE_CAP * 2)
}

export function isInert(tokensBefore: number | null, tokensAfter: number | null): boolean {
  if (tokensBefore === null || tokensAfter === null || tokensBefore <= 0) return false
  return tokensAfter / tokensBefore >= INERT_KEPT_RATIO
}

export function fillBar(fill: number | null): string {
  if (fill === null) return ''
  const filled = Math.min(10, Math.max(0, Math.round(fill / 10)))
  return '▮'.repeat(filled) + '▯'.repeat(10 - filled)
}

export type NextInput = {
  now: number
  effective: Effective
  all: AllSessions
  phase: Phase
  ledger: Ledger
  heldCount: number
  clock: (at: number) => string
}

export function nextLine(input: NextInput): string {
  const { effective: here, ledger, phase } = input
  if (phase === 'composing') return 'now: choosing what to keep'
  if (phase === 'summarizing') return 'now: summarizing'
  if (ledger.isNowAfterTurn) return 'at the end of this turn'
  if (!here.isOn) return 'never, until turned on'
  if (ledger.isInert) return 'never: the last summary kept most of the conversation; press n to summarize now'
  const at = `when a turn ends above ${here.floor}%`
  if (ledger.heldSince !== null && input.heldCount > 0) {
    const left = Math.max(0, Math.ceil((ledger.heldSince + input.all.holdMs - input.now) / 60_000))
    const agents = input.heldCount === 1 ? '1 helper agent' : `${input.heldCount} helper agents`
    return `waiting for ${agents}, until ${input.all.ceiling}% or ${left} min`
  }
  if (ledger.lastAttemptAt !== null && input.now - ledger.lastAttemptAt < COOLDOWN_MS) {
    return `summarized ${at}, from ${input.clock(ledger.lastAttemptAt + COOLDOWN_MS)}`
  }
  return `summarized ${at}`
}
