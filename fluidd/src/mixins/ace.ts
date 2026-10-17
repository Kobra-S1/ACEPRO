import Vue from 'vue'
import Component from 'vue-class-component'

export interface AceSlot {
  index: number
  tool: number
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
  duration: number // seconds
  remain_time: number // seconds
}

export interface AceInstanceState {
  status?: string
  model?: string
  firmware?: string
  connected?: boolean
  temp?: number
  fan_speed?: number
  enable_rfid?: number
  current_tool?: number
  dryer?: AceDryer
  slots?: AceSlot[]
}

// Filament path positions are rendered by the card directly from sensors.

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

  get currentInstanceIndex (): number {
    const idx = this.aceManager?.current_index
    // In the acepro-any-klipper driver current_index is the index of the
    // CURRENT TOOL (global), not of the instance. Clamp the range for
    // compatibility with possible other drivers.
    if (typeof idx === 'number' && idx >= 0 && this.aceInstanceCount > 1) {
      return Math.floor(idx / 4)
    }
    return 0
  }

  aceInstance (index: number): AceInstanceState | undefined {
    return this.printer[`ace_instance_${index}`]
  }

  // Currently loaded tool (global Tn index).
  // The acepro-any-klipper driver reports it in printer.ace.current_index.
  get currentTool (): number {
    const mgr: any = this.aceManager
    for (const key of ['current_tool', 'cur_tool', 'active_tool', 'tool']) {
      const v = mgr?.[key]
      if (typeof v === 'number' && v >= 0) return v
    }
    if (typeof mgr?.current_index === 'number' && mgr.current_index >= 0) {
      return mgr.current_index
    }
    const inst: any = this.aceInstance(this.currentInstanceIndex)
    for (const key of ['current_tool', 'cur_tool', 'active_tool']) {
      const v = inst?.[key]
      if (typeof v === 'number' && v >= 0) return v
    }
    return this.TOOL_UNKNOWN
  }

  slotsOf (instanceIndex: number): AceSlot[] {
    const raw = this.aceInstance(instanceIndex)?.slots
    if (!Array.isArray(raw)) return []
    return raw.map((s: any, i: number) => ({
      index: typeof s?.index === 'number' ? s.index : i,
      tool: typeof s?.tool === 'number' ? s.tool : instanceIndex * 4 + i,
      status: String(s?.status ?? ''),
      material: String(s?.material ?? s?.type ?? ''),
      color: Array.isArray(s?.color) ? s.color : [0, 0, 0],
      sku: String(s?.sku ?? ''),
      rfid: s?.rfid ?? false,
      temp: Number(s?.temp ?? 0)
    }))
  }

  dryerOf (instanceIndex: number): AceDryer {
    const inst: any = this.aceInstance(instanceIndex)
    const d: any = inst?.dryer_status ?? inst?.dryer ?? {}
    return {
      status: String(d?.status ?? 'stop'),
      target_temp: Number(d?.target_temp ?? 0),
      duration: Number(d?.duration ?? 0),
      remain_time: Number(d?.remain_time ?? 0)
    }
  }

  toolIndex (instanceIndex: number, slotIndex: number): number {
    return instanceIndex * 4 + slotIndex
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

  // --- Filament path sensors (splitter/RDM, encoder, nozzle) ---
  // Object names are matched tolerantly: multi-ACE configs may name
  // sensors filament_runout_rdm_1 etc.

  sensorByName (fragment: string): boolean | null {
    const key = Object.keys(this.printer).find(k =>
      k.toLowerCase().includes(fragment.toLowerCase()) &&
      (k.startsWith('filament_switch_sensor') || k.startsWith('filament_motion_sensor'))
    )
    if (!key) return null
    const v = this.printer[key]?.filament_detected
    return typeof v === 'boolean' ? v : null
  }

  // true/false = filament at the sensor; null = sensor not found
  get rdmSensor (): boolean | null {
    return this.sensorByName('runout_rdm')
  }

  get feederSensor (): boolean | null {
    return this.sensorByName('ace_encoder') ?? this.sensorByName('tracker')
  }

  get nozzleSensor (): boolean | null {
    return this.sensorByName('runout_nozzle')
  }

  // Slot with feed assist enabled (slot number or -1).
  // null = field not found in any instance.
  get feedAssistSlot (): number | null {
    for (let i = 0; i < this.aceInstanceCount; i++) {
      const v: any = this.aceInstance(i)?.feed_assist_slot
      if (typeof v === 'number') return v
    }
    return null
  }
}
