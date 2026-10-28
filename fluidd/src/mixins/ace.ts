import Vue from 'vue'
import Component from 'vue-class-component'

export interface AceSlot {
  index: number
  tool: number // global T<n>; tool numbers need not start at unit * 4
  status: string
  material: string
  color: number[] // [r, g, b]
  sku: string
  rfid: number | boolean
  temp: number
}

export interface AceDryer {
  status: string // 'stop' | 'drying' | ...
  target_temp: number
  remain_time: number // seconds
}

export interface AceInstanceState {
  status?: string
  model?: string
  firmware?: string
  connection_state?: string
  temp?: number
  feed_assist_slot?: number
  max_dryer_temperature?: number
  dryer_status?: AceDryer
  slots?: AceSlot[]
}

export interface AceSlotLocation {
  instance: number
  slot: number
}

// Dryer limit for a driver too old to report max_dryer_temperature: the
// lowest limit the shipped configs use, so the slider never offers a
// temperature the driver rejects.
const FALLBACK_MAX_DRYER_TEMP = 55

@Component({})
export default class AceMixin extends Vue {
  readonly TOOL_UNKNOWN: number = -1

  get printer (): Record<string, any> {
    return (this.$typedState.printer.printer as any) ?? {}
  }

  get aceManager (): Record<string, any> | undefined {
    return this.printer.ace
  }

  get hasAce (): boolean {
    return this.aceManager != null
  }

  get aceInstanceCount (): number {
    const n = this.aceManager?.ace_instances
    return typeof n === 'number' && n > 0 ? n : 1
  }

  aceInstance (index: number): AceInstanceState | undefined {
    return this.printer[`ace_instance_${index}`]
  }

  // Currently loaded tool (global T<n>), -1 when none.
  get currentTool (): number {
    const idx = this.aceManager?.current_index
    return typeof idx === 'number' && idx >= 0 ? idx : this.TOOL_UNKNOWN
  }

  slotsOf (instanceIndex: number): AceSlot[] {
    const raw = this.aceInstance(instanceIndex)?.slots
    if (!Array.isArray(raw)) return []
    return raw.map((s: any, i: number) => ({
      index: typeof s?.index === 'number' ? s.index : i,
      tool: typeof s?.tool === 'number' ? s.tool : this.TOOL_UNKNOWN,
      status: String(s?.status ?? ''),
      material: String(s?.material ?? ''),
      color: Array.isArray(s?.color) ? s.color : [0, 0, 0],
      sku: String(s?.sku ?? ''),
      rfid: s?.rfid ?? false,
      temp: Number(s?.temp ?? 0)
    }))
  }

  // Unit and slot holding a tool; null for a tool no ACE feeds (e.g. a
  // printer-side tool changer's direct inlet).
  toolLocation (tool: number): AceSlotLocation | null {
    if (tool < 0) return null
    for (let i = 0; i < this.aceInstanceCount; i++) {
      const slot = this.slotsOf(i).find(s => s.tool === tool)
      if (slot) return { instance: i, slot: slot.index }
    }
    return null
  }

  get currentToolLocation (): AceSlotLocation | null {
    return this.toolLocation(this.currentTool)
  }

  dryerOf (instanceIndex: number): AceDryer {
    const d: any = this.aceInstance(instanceIndex)?.dryer_status ?? {}
    return {
      status: String(d?.status ?? 'stop'),
      target_temp: Number(d?.target_temp ?? 0),
      remain_time: Number(d?.remain_time ?? 0)
    }
  }

  maxDryerTemp (instanceIndex: number): number {
    const t = Number(this.aceInstance(instanceIndex)?.max_dryer_temperature)
    return t > 0 ? t : FALLBACK_MAX_DRYER_TEMP
  }

  slotColorCss (slot?: AceSlot): string {
    const c = slot?.color
    if (Array.isArray(c) && c.length >= 3 && c.some(v => Number(v) > 0)) {
      return `rgb(${c[0]}, ${c[1]}, ${c[2]})`
    }
    return 'transparent'
  }

  slotEmpty (slot?: AceSlot): boolean {
    const s = (slot?.status ?? '').toLowerCase()
    return s === '' || ['empty', 'unknown', 'none', 'no'].includes(s)
  }

  isDryerRunning (instanceIndex: number): boolean {
    return ['running', 'on', 'active', 'drying', 'work'].includes(
      this.dryerOf(instanceIndex).status.toLowerCase()
    )
  }

  // --- Filament path sensors ---
  // The driver reports the toolhead and splitter (RDM) sensor states under
  // whatever names the config gives them; null = sensor not configured.

  get rdmSensor (): boolean | null {
    const v = this.aceManager?.rdm_sensor
    return typeof v === 'boolean' ? v : null
  }

  get nozzleSensor (): boolean | null {
    const v = this.aceManager?.toolhead_sensor
    return typeof v === 'boolean' ? v : null
  }

  // An optional encoder is not part of the ACE driver: match it by name.
  get feederSensor (): boolean | null {
    const key = Object.keys(this.printer).find(k =>
      (k.startsWith('filament_switch_sensor') || k.startsWith('filament_motion_sensor')) &&
      ['ace_encoder', 'tracker'].some(f => k.toLowerCase().includes(f))
    )
    if (!key) return null
    const v = this.printer[key]?.filament_detected
    return typeof v === 'boolean' ? v : null
  }
}
