import { atom, read, update } from 'claude-code'
import type { EngineInterface, PluginOptions, Register } from 'claude-code'

import type { Ledger, Outcome, Overrides, Phase, Trigger, TurnFacts } from '../types'
import {
  AUTO_RESUME_GAP_MS,
  FORK_RACE_MS,
  GENERIC_RESUME,
  clampFloor,
  closesOnQuestion,
  decideAuto,
  defusePrefix,
  effective,
  allOptions,
  fallbackFocus,
  focusPrompt,
  hereOptions,
  inFlightDispatches,
  isInert,
  nextLine,
  parseCommand,
  parseFocus,
  readFloor,
  readStart,
  startOptions,
  startQuestion,
} from './rules'
import type { AllSessions, Command } from './rules'

const PLUGIN = 'continuous-compaction'
const KEY_ARMED = `${PLUGIN}.armed`
const KEY_FLOOR = `${PLUGIN}.pct_floor`

const EMPTY_LEDGER: Ledger = {
  lastAttemptAt: null,
  heldSince: null,
  isInert: false,
  isNowAfterTurn: false,
  isTurnRunning: false,
  lastAutoResumeAt: null,
  last: null,
}

const overrides = atom({ plugin: 'continuous-compaction', key: 'overrides' } as const, { enabled: null, floor: null } as Overrides)
const fill = atom({ plugin: 'continuous-compaction', key: 'fill' } as const, null as number | null)
const phase = atom({ plugin: 'continuous-compaction', key: 'phase' } as const, 'idle' as Phase)
const turnFacts = atom({ plugin: 'continuous-compaction', key: 'turnFacts' } as const, null as TurnFacts | null)
const ledger = atom({ plugin: 'continuous-compaction', key: 'ledger' } as const, EMPTY_LEDGER)

function allSessionsFrom(options: PluginOptions): AllSessions {
  const number = (key: string, fallback: number) => (typeof options[key] === 'number' ? (options[key] as number) : fallback)
  return {
    isArmed: options.armed !== false,
    floor: clampFloor(number('pct_floor', 80)),
    ceiling: clampFloor(number('pct_ceiling', 85)),
    holdMs: number('task_stale_s', 1800) * 1000,
  }
}

function clockTime(at: number): string {
  const date = new Date(at)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}


async function record($: EngineInterface, outcome: Outcome) {
  await update($, ledger, value => ({ ...value, last: outcome }))
  $.ui.log(`${outcome.trigger} ${outcome.kind} ${JSON.stringify(outcome)}`, { to: 'debug' })
}

async function compose($: EngineInterface, facts: TurnFacts | null) {
  const dispatched = facts?.dispatched ?? []
  const timedOut = $.clock.sleep(FORK_RACE_MS).then(() => null)
  const answered = $.model
    .fork({ prompt: focusPrompt(dispatched) })
    .then(result => (result.isAnswered ? parseFocus(result.text) : null))
    .catch(() => null)
  const parsed = await Promise.race([answered, timedOut])
  if (parsed) return { focus: parsed.focus, resume: parsed.resume, focusFrom: 'fork' as const }
  return { focus: fallbackFocus(dispatched), resume: '', focusFrom: 'fallback' as const }
}

async function resumeAfter($: EngineInterface, resume: string, closingText: string): Promise<boolean> {
  if (closesOnQuestion(closingText)) return false
  const box = await $.prompt.read().catch(() => null)
  if (!box || box.text.trim()) return false
  void $.prompt.submit({ text: defusePrefix(resume || GENERIC_RESUME) }).catch(() => $.ui.toast("Couldn't continue after the summary."))
  return true
}

async function summarize($: EngineInterface, trigger: Trigger) {
  let isClaimed = false
  await update($, phase, value => {
    isClaimed = value === 'idle'
    return isClaimed ? 'composing' : value
  })
  if (!isClaimed) return
  try {
    $.ui.status('Choosing what to keep')
    const facts = await read($, turnFacts)
    const { focus, resume, focusFrom } = await compose($, facts)
    $.ui.log(`${trigger} focus from ${focusFrom}: ${focus}`, { to: 'debug' })
    await update($, phase, () => 'summarizing')
    $.ui.status('Summarizing the conversation')
    const result = await $.session.compact({ instructions: focus })
    const at = await $.clock.now()
    if (result.skip !== undefined) {
      await record($, { kind: 'skipped', at, trigger, reason: result.skip })
      $.ui.toast(`Not summarized: ${result.skip}`)
      return
    }
    const tokensBefore = result.tokensBefore ?? null
    const tokensAfter = result.tokensAfter ?? null
    const kept = isInert(tokensBefore, tokensAfter)
    await update($, ledger, value => ({ ...value, isInert: kept, heldSince: null }))
    const isResumed = await resumeAfter($, resume, facts?.closingText ?? '')
    await record($, { kind: 'summarized', at, trigger, tokensBefore, tokensAfter, focusFrom, isInert: kept, isResumed })
    $.ui.toast('The conversation was summarized.')
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    await record($, { kind: 'failed', at: await $.clock.now(), trigger, reason })
    $.ui.toast(`Couldn't summarize the conversation: ${reason}`)
  } finally {
    const at = await $.clock.now()
    await update($, ledger, value => ({ ...value, lastAttemptAt: at }))
    await update($, phase, () => 'idle')
    $.ui.status(undefined)
  }
}

async function summarizeNow($: EngineInterface): Promise<'now' | 'after-turn'> {
  const { isTurnRunning } = await read($, ledger)
  if (isTurnRunning) {
    await update($, ledger, value => ({ ...value, isNowAfterTurn: true }))
    return 'after-turn'
  }
  void summarize($, 'now')
  return 'now'
}

async function setAll($: EngineInterface, key: string, value: boolean | number): Promise<string | null> {
  const result = await $.config.set({ key, value })
  return 'deny' in result && result.deny ? result.deny : null
}

async function runCommand($: EngineInterface, all: AllSessions, command: Command): Promise<string | undefined> {
  if (command.kind === 'open') return undefined
  if (command.kind === 'reset') {
    await update($, overrides, () => ({ enabled: null, floor: null }))
    return `This conversation follows all conversations: ${all.isArmed ? `above ${all.floor}%` : 'off'}.`
  }
  if (command.kind === 'now') {
    return (await summarizeNow($)) === 'now' ? 'Summarizing now.' : 'Summarizing at the end of this turn.'
  }
  if (command.kind === 'switch' && command.scope === 'here') {
    await update($, overrides, value => ({ ...value, enabled: command.isOn }))
    return `This conversation: ${command.isOn ? 'on' : 'off'}.`
  }
  if (command.kind === 'switch') {
    return (await setAll($, KEY_ARMED, command.isOn)) ?? `All conversations: ${command.isOn ? 'on' : 'off'}.`
  }
  if (command.scope === 'here') {
    await update($, overrides, value => ({ ...value, floor: command.value }))
    return `This conversation is summarized above ${command.value}%.`
  }
  return (await setAll($, KEY_FLOOR, command.value)) ?? `All conversations are summarized above ${command.value}%.`
}

async function askDialog($: EngineInterface, all: AllSessions): Promise<string | undefined> {
  const here = effective(await read($, overrides), all)
  const next = nextLine({
    now: await $.clock.now(),
    effective: here,
    all,
    phase: await read($, phase),
    ledger: await read($, ledger),
    heldCount: (await read($, turnFacts))?.dispatched.length ?? 0,
    clock: clockTime,
  })
  const question = startQuestion(here, all, await read($, fill), next)
  const started = await $.ui.ask(question, { header: 'Summary', options: startOptions(here) }).catch(() => null)
  if (started === null) return undefined
  const first = readStart(started, here)
  if ('usage' in first) return first.usage
  if ('kind' in first) return runCommand($, all, first)
  const asked = first.ask === 'here' ? 'Summarize this conversation above which %?' : 'Summarize all conversations above which %?'
  const options = first.ask === 'here' ? hereOptions(here, all) : allOptions(all)
  const picked = await $.ui.ask(asked, { header: 'Summary', options }).catch(() => null)
  if (picked === null) return undefined
  const chosen = readFloor(first, picked, all)
  return 'usage' in chosen ? chosen.usage : runCommand($, all, chosen)
}

export const register: Register = (on, options) => {
  const all = allSessionsFrom(options)

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({
      name: 'compaction',
      description: 'See and change when this conversation is summarized',
      argumentHint: '[<1-100> | all <1-100> | on | off | all on | all off | reset | fire]',
      immediate: true,
    })
    const stuck = await read($, phase)
    if (stuck !== 'idle') {
      await update($, phase, () => 'idle')
      await record($, { kind: 'failed', at: await $.clock.now(), trigger: 'auto', reason: 'reloaded while summarizing' })
    }
    return started
  })

  on('turn.start', async ($, e, next) => {
    await update($, turnFacts, () => null)
    await update($, ledger, value => ({ ...value, isTurnRunning: true }))
    return next(e)
  })

  on('classic.Stop', async ($, e, next) => {
    const result = await next(e)
    if (e.agent_id !== undefined || ('decision' in result && result.decision === 'block')) return result
    const tasks = (e.background_tasks ?? []).map(task => ({
      id: task.id,
      type: task.type,
      status: task.status,
      description: task.description,
    }))
    await update($, turnFacts, () => ({
      dispatched: inFlightDispatches(tasks),
      isPlanMode: e.permission_mode === 'plan',
      closingText: e.last_assistant_message ?? '',
    }))
    return result
  }).catch(($, e, next) => next(e))

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId !== undefined) return result
    const usage = await $.session.usage().catch(() => null)
    const reading = usage?.context.percent ?? null
    await update($, fill, () => reading)
    const { isNowAfterTurn } = await read($, ledger)
    const pending = await update($, ledger, value => ({ ...value, isTurnRunning: false, isNowAfterTurn: false }))
    if (isNowAfterTurn) {
      await summarize($, 'now')
      return result
    }
    const facts = await read($, turnFacts)
    const verdict = decideAuto({
      now: await $.clock.now(),
      reason: e.reason,
      effective: effective(await read($, overrides), all),
      all,
      fill: reading,
      isPlanMode: facts?.isPlanMode ?? false,
      dispatched: facts?.dispatched ?? [],
      phase: await read($, phase),
      ledger: pending,
    })
    if (verdict.act === 'hold') {
      await update($, ledger, value => ({ ...value, heldSince: verdict.heldSince }))
    } else if (verdict.act === 'wait') {
      if (verdict.why === 'below-floor') await update($, ledger, value => ({ ...value, heldSince: null }))
      $.ui.log(`auto wait ${verdict.why} fill=${reading ?? 'none'}`, { to: 'debug' })
    } else {
      $.ui.log(`auto fire released=${verdict.releasedBy} fill=${reading}`, { to: 'debug' })
      await summarize($, 'auto')
    }
    return result
  })

  on('session.compact', async ($, e, next) => {
    const result = await next(e)
    if (e.trigger !== 'auto' || e.agentId !== undefined || result.skip !== undefined) return result
    const now = await $.clock.now()
    const { lastAutoResumeAt } = await read($, ledger)
    if (lastAutoResumeAt !== null && now - lastAutoResumeAt < AUTO_RESUME_GAP_MS) return result
    const facts = await read($, turnFacts)
    if (await resumeAfter($, '', facts?.closingText ?? '')) {
      await update($, ledger, value => ({ ...value, lastAutoResumeAt: now }))
    }
    return result
  }).catch(($, e, next) => next(e))

  on('session.measure', async ($, e, next) => {
    await update($, fill, () => e.context.percent ?? null)
    return next(e)
  })

  on('config.describe', { key: 'continuous-compaction.armed' }, ($, e, next) =>
    next({ ...e, label: 'Summarize conversations automatically', description: 'Summarize a conversation when a turn ends above the % below.' }),
  )
  on('config.describe', { key: 'continuous-compaction.pct_floor' }, ($, e, next) =>
    next({ ...e, label: 'Summarize at % full', description: 'How full the context must be when a turn ends. /compaction changes it for one conversation.' }),
  )
  on('config.describe', { key: 'continuous-compaction.pct_ceiling' }, ($, e, next) => next({ ...e, isHidden: true }))
  on('config.describe', { key: 'continuous-compaction.task_stale_s' }, ($, e, next) => next({ ...e, isHidden: true }))

  on('command.run', { command: 'compaction' }, async ($, e) => {
    const parsed = parseCommand(e.args)
    if ('usage' in parsed) return { text: parsed.usage }
    const text = parsed.kind === 'open' ? await askDialog($, all) : await runCommand($, all, parsed)
    return text === undefined ? {} : { text }
  })
}
