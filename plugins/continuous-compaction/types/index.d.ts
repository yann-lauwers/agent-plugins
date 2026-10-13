export type Overrides = { enabled: boolean | null; floor: number | null }

export type Phase = 'idle' | 'composing' | 'summarizing'

export type Trigger = 'auto' | 'now'

export type Dispatch = { id: string; type: string; description: string }

export type TurnFacts = { dispatched: readonly Dispatch[]; isPlanMode: boolean; closingText: string }

export type Outcome =
  | {
      kind: 'summarized'
      at: number
      trigger: Trigger
      tokensBefore: number | null
      tokensAfter: number | null
      focusFrom: 'fork' | 'fallback'
      isInert: boolean
      isResumed: boolean
    }
  | { kind: 'skipped' | 'failed'; at: number; trigger: Trigger; reason: string }

export type Ledger = {
  lastAttemptAt: number | null
  heldSince: number | null
  isInert: boolean
  isNowAfterTurn: boolean
  isTurnRunning: boolean
  lastAutoResumeAt: number | null
  last: Outcome | null
}

export type RowAction =
  | { act: 'step'; row: 'here' | 'all'; delta: number }
  | { act: 'toggle'; row: 'here' | 'all' }
  | { act: 'reset' }
  | { act: 'now' }

export type RowView = {
  id: 'here' | 'all'
  label: string
  isOn: boolean
  isOnInherited: boolean
  floor: number
  isFloorInherited: boolean
}

export type RowsProps = { rows: readonly RowView[] }

declare module 'claude-code' {
  interface PluginState {
    'continuous-compaction': {
      overrides: Overrides
      fill: number | null
      phase: Phase
      turnFacts: TurnFacts | null
      ledger: Ledger
    }
  }
}
