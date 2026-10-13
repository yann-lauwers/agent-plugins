import type { ClientModule, ClientKeyEvent, ClientSurface } from 'claude-code'

import type { RowAction, RowsProps, RowView } from '../types'
import { FLOOR_STEP } from './rules'

type Selection = { selected: number }

function actionFor(event: ClientKeyEvent, row: RowView): RowAction | null {
  if (event.key === 'left') return { act: 'step', row: row.id, delta: -FLOOR_STEP }
  if (event.key === 'right') return { act: 'step', row: row.id, delta: FLOOR_STEP }
  if (event.key === 'd') return { act: 'toggle', row: row.id }
  if (event.key === 'r') return { act: 'reset' }
  if (event.key === 'n') return { act: 'now' }
  return null
}

function listen(props: RowsProps, surface: ClientSurface<Selection>) {
  surface.onKey(event => {
    const selected = surface.state?.selected ?? 0
    if (event.key === 'up' || event.key === 'down') {
      const moved = event.key === 'up' ? selected - 1 : selected + 1
      surface.setState({ selected: Math.min(props.rows.length - 1, Math.max(0, moved)) })
      return
    }
    const row = props.rows[selected]
    const action = row ? actionFor(event, row) : null
    if (action) surface.post(action)
  })
  surface.setState({ selected: 0 })
}

const Rows: ClientModule<RowsProps, Selection> = (props, surface) => {
  if (surface.state === undefined) listen(props, surface)
  const { Box, Text } = surface.elements
  const selected = surface.state?.selected ?? 0

  return Box({
    flexDirection: 'column',
    children: props.rows.map((row, index) => {
      const isSelected = index === selected
      const state = row.isOn ? 'On' : 'Off'
      const floor = `${row.floor}%${row.isFloorInherited ? ' (all)' : ''}`
      return Box({
        flexDirection: 'row',
        gap: 2,
        children: [
          Text({ children: `${isSelected ? '▸' : ' '} ${row.label.padEnd(18)}`, bold: isSelected }),
          Text({ children: `${state}${row.isOnInherited ? ' (all)' : ''}`.padEnd(9), dimColor: !row.isOn }),
          Text({ children: isSelected ? `‹ ${floor} ›` : `  ${floor}`, bold: isSelected }),
        ],
      })
    }),
  })
}

export default Rows
