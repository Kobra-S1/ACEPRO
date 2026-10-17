<template>
  <collapsable-card
    title="ACE Pro"
    icon="$cogs"
    draggable
    layout-path="dashboard.ace-card"
  >
    <template #menu>
      <v-menu
        bottom
        left
        offset-y
        transition="slide-y-transition"
      >
        <template #activator="{ on, attrs }">
          <app-btn
            :disabled="!klippyReady"
            v-bind="attrs"
            small
            class="me-1 my-1"
            v-on="on"
          >
            <v-icon
              small
              class="me-1"
            >
              $dotsVertical
            </v-icon>
            Actions
            <v-icon
              small
              class="ms-1"
            >
              $chevronDown
            </v-icon>
          </app-btn>
        </template>

        <v-list dense>
          <v-list-item
            :disabled="!canService"
            @click="send('ACE_SMART_LOAD')"
          >
            <v-list-item-title>Load all slots</v-list-item-title>
          </v-list-item>
          <v-list-item
            :disabled="!canService"
            @click="send('ACE_FULL_UNLOAD TOOL=ALL')"
          >
            <v-list-item-title>Full unload all</v-list-item-title>
          </v-list-item>
          <v-list-item
            :disabled="!canService"
            @click="send('ACE_RECONNECT')"
          >
            <v-list-item-title>Reconnect ACE</v-list-item-title>
          </v-list-item>
          <v-divider />
          <v-list-item
            :disabled="!canSend"
            @click="send('ACE_GET_STATUS')"
          >
            <v-list-item-title>Request status</v-list-item-title>
          </v-list-item>
        </v-list>
      </v-menu>
    </template>

    <div :class="{ 'ace-disabled': !klippyReady }">
      <v-container
        fluid
        pa-2
      >
        <!-- Header: model / firmware / connection -->
        <v-row
          dense
          align="center"
          class="ace-header smaller-font"
        >
          <v-col class="text--secondary text-truncate">
            MODEL: {{ modelName }}
          </v-col>
          <v-col
            cols="auto"
            class="d-flex align-center"
          >
            <v-chip
              v-if="connectionState"
              x-small
              :color="isConnected ? 'success' : 'error'"
              outlined
              class="me-2"
            >
              {{ isConnected ? 'Connected' : 'Disconnected' }}
            </v-chip>
            <span class="text--secondary">FIRMWARE: {{ firmwareVersion }}</span>
          </v-col>
        </v-row>

        <template v-for="i in aceInstanceCount">
          <div
            v-if="aceInstanceCount > 1"
            :key="'ace-hdr-' + i"
            class="text--disabled subtitle-2 mt-2"
          >
            ACE #{{ i }} · T{{ (i - 1) * 4 }}–T{{ (i - 1) * 4 + 3 }}
          </div>

          <!-- Slots: click on spool = load Tn, pencil = color/material -->
          <v-row
            :key="'ace-slots-' + i"
            dense
            class="mt-1"
          >
            <v-col
              v-for="slot in slotsOf(i - 1)"
              :key="slot.index"
              cols="3"
              class="d-flex"
            >
              <div class="ace-slot-tile">
                <div class="ace-slot-label text--disabled">
                SLOT {{ slot.index }}
              </div>
              <div
                class="ace-slot-status"
                :class="statusClass(i - 1, slot)"
              >
                {{ slotStatusText(i - 1, slot) }}
              </div>
              <div class="ace-spool-wrap">
                <button
                  type="button"
                  class="ace-spool"
                  :class="spoolClasses(i - 1, slot)"
                  :style="{ borderColor: spoolBorderCss(slot) }"
                  :disabled="!canSelectTool"
                  @click="selectTool(slotTool(slot))"
                >
                  <span class="ace-spool-inner" />
                </button>
                <button
                  type="button"
                  class="ace-spool-edit"
                  :disabled="!canSend"
                  @click.stop="openSlotDialog(i - 1, slot)"
                >
                  <v-icon x-small>
                    $pencil
                  </v-icon>
                </button>
              </div>
              <div class="ace-slot-material text-center">
                {{ slotEmpty(slot) ? '—' : (slot.material || '—') }}
              </div>
              <div class="ace-slot-sub text--disabled text-center">
                <template v-if="slot.rfid">
                  RFID · {{ slot.temp }}°C
                </template>
                <template v-else-if="slot.temp > 0">
                  {{ slot.temp }}°C
                </template>
                <template v-else>
                  &nbsp;
                </template>
                </div>
              </div>
            </v-col>
          </v-row>

          <v-divider
            v-if="i < aceInstanceCount"
            :key="'ace-div-' + i"
            class="my-2"
          />
        </template>

        <!-- Filament path: ACE -> [SPLITTER] -> [Encoder] -> [Nozzle]
             Nodes appear only when the matching sensor exists in config. -->
        <template v-if="showPath">
          <v-divider class="my-2" />
          <div class="ace-path">
            <div
              class="ace-path-node"
              :class="{ active: pathInAce }"
            >
              <div
                class="ace-path-dot"
                :style="pathInAce && currentToolColor ? { background: currentToolColor } : {}"
              />
              <div class="ace-path-caption">
                ACE
              </div>
            </div>
            <template v-if="showRdmNode">
              <div
                class="ace-path-line"
                :class="{ active: rdmSensor === true || flowToSplitter, busy: flowToSplitter }"
              >
                <div class="ace-path-flow" />
              </div>
              <div
                class="ace-path-node"
                :class="{ active: rdmSensor === true, empty: rdmSensor === false }"
              >
                <div class="ace-path-dot" />
                <div class="ace-path-caption">
                  SPLITTER
                </div>
              </div>
            </template>
            <template v-if="showEncNode">
              <div
                class="ace-path-line"
                :class="{ active: filamentPastEncoder || flowToEncoder, busy: flowToEncoder }"
              >
                <div class="ace-path-flow" />
              </div>
              <div
                class="ace-path-node"
                :class="{ active: filamentAtEncoder, jam: encoderJam }"
              >
                <div
                  class="ace-enc-wheel"
                  :class="{ active: filamentAtEncoder, jam: encoderJam }"
                >
                  <div class="ace-enc-spoke" />
                </div>
                <div class="ace-path-caption">
                  Encoder
                </div>
              </div>
            </template>
            <template v-if="showNozzleNode">
              <div
                class="ace-path-line"
                :class="{ active: filamentAtNozzle || flowToNozzle, busy: flowToNozzle }"
              >
                <div class="ace-path-flow" />
              </div>
              <div
                class="ace-path-node"
                :class="{ active: nozzleSensor === true, empty: nozzleSensor === false }"
              >
                <div class="ace-path-dot" />
                <div class="ace-path-caption">
                  Nozzle
                </div>
              </div>
            </template>
          </div>
        </template>

        <v-divider class="my-2" />

        <!-- Controls: unload / feed assist toggle / dryer -->
        <v-row
          dense
          align="center"
        >
          <v-col
            cols="6"
            sm="4"
          >
            <app-btn
              block
              small
              :disabled="!canService || currentTool < 0"
              @click="send('ACE_SMART_UNLOAD')"
            >
              Unload
            </app-btn>
          </v-col>
          <v-col
            cols="6"
            sm="4"
          >
            <div
              class="ace-fa-switch"
              :class="{ 'ace-fa-enabled': feedAssistOn, 'ace-fa-disabled': !canService || currentTool < 0 }"
              @click="toggleFeedAssist(!feedAssistOn)"
            >
              <div
                class="ace-fa-seg"
                :class="{ active: !feedAssistOn }"
              >
                OFF
              </div>
              <div
                class="ace-fa-seg"
                :class="{ active: feedAssistOn }"
              >
                FEED ASSIST
              </div>
            </div>
          </v-col>
          <v-col
            cols="12"
            sm="4"
          >
            <app-btn
              block
              small
              :disabled="!klippyReady"
              @click="openDryerDialog"
            >
              <v-icon
                small
                class="me-1"
              >
                $thermometer
              </v-icon>
              Dryer
            </app-btn>
          </v-col>
        </v-row>
      </v-container>
    </div>

    <!-- Dialog: slot color and material -->
    <v-dialog
      v-model="slotDialog.open"
      max-width="420"
    >
      <v-card>
        <v-card-title class="text-h6">
          Slot {{ slotDialog.slotIndex }} · Filament
        </v-card-title>
        <v-card-text>
          <v-select
            v-model="slotDialog.material"
            :items="materialItems"
            label="Material type"
            dense
            hide-details
            class="mb-4"
          />
          <div class="d-flex align-center mb-3">
            <div
              class="ace-dlg-ring"
              :style="{ borderColor: slotDialogCssColor }"
            >
              <div class="ace-dlg-ring-inner" />
            </div>
            <span class="ms-4 text--secondary">R {{ slotDialog.color[0] }} · G {{ slotDialog.color[1] }} · B {{ slotDialog.color[2] }}</span>
          </div>
          <div class="d-flex flex-wrap">
            <button
              v-for="(c, idx) in swatches"
              :key="idx"
              type="button"
              class="ace-swatch"
              :style="{ background: c, borderColor: swatchBorder(c) }"
              @click="pickSwatch(c)"
            />
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <app-btn
            text
            @click="slotDialog.open = false"
          >
            Cancel
          </app-btn>
          <app-btn
            color="primary"
            :disabled="!canSend || busy"
            @click="saveSlotDialog"
          >
            Save
          </app-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- Dialog: dryer -->
    <v-dialog
      v-model="dryerDialog"
      max-width="440"
    >
      <v-card>
        <v-card-title class="text-h6">
          ACE Pro Dryer
        </v-card-title>
        <v-card-text>
          <v-row
            dense
            align="center"
          >
            <v-col>
              <div class="text--disabled ace-dlg-label">
                CHAMBER
              </div>
              <div class="ace-chamber-temp">
                {{ chamberTemp(dryerInstance) }}°C
              </div>
            </v-col>
            <v-col class="text-right">
              <v-chip
                small
                :color="dryerRunning ? 'orange' : undefined"
                :outlined="!dryerRunning"
              >
                {{ dryerRunning ? 'DRYING' : 'IDLE' }}
              </v-chip>
              <div
                v-if="dryerRunning"
                class="text--disabled smaller-font mt-1"
              >
                {{ remainText(dryerInstance) }} left
              </div>
            </v-col>
          </v-row>

          <div class="d-flex align-center mt-2">
            <span class="ace-dlg-slider-label">Temperature</span>
            <v-slider
              v-model="dryTemp"
              min="50"
              max="65"
              step="5"
              hide-details
              dense
              class="mx-3 flex-grow-1"
            />
            <span class="ace-dlg-slider-val">{{ dryTemp }}°C</span>
          </div>
          <div class="d-flex align-center">
            <span class="ace-dlg-slider-label">Time</span>
            <v-slider
              v-model="dryDuration"
              min="30"
              max="240"
              step="30"
              hide-details
              dense
              class="mx-3 flex-grow-1"
            />
            <span class="ace-dlg-slider-val">{{ dryDuration }} min</span>
          </div>
        </v-card-text>
        <v-card-actions>
          <app-btn
            v-if="!dryerRunning"
            block
            color="primary"
            :disabled="!canSend || busy"
            @click="startDrying"
          >
            START DRYING
          </app-btn>
          <app-btn
            v-else
            block
            color="error"
            :disabled="!canSend || busy"
            @click="stopDrying"
          >
            STOP · {{ remainText(dryerInstance) }} left
          </app-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </collapsable-card>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import StateMixin from '@/mixins/state'
import AceMixin from '@/mixins/ace'
import type { AceSlot } from '@/mixins/ace'

interface SlotDialogState {
  open: boolean
  instance: number
  slotIndex: number
  tool: number
  material: string
  color: number[]
}

@Component({})
export default class AceCard extends Mixins(StateMixin, AceMixin) {
  busy = false
  faLocal: boolean | null = null
  dryerDialog = false
  dryerInstance = 0
  dryTemp = 55
  dryDuration = 240

  slotDialog: SlotDialogState = {
    open: false,
    instance: 0,
    slotIndex: 0,
    tool: 0,
    material: 'PLA',
    color: [0, 0, 0]
  }

  readonly materials = ['PLA', 'PETG', 'ABS', 'ASA', 'PC', 'PA', 'TPU', 'Custom']

  // Black + white first, then rainbow
  readonly swatches = [
    '#212121', '#F5F5F5', '#E53935', '#FB8C00', '#FDD835',
    '#43A047', '#00ACC1', '#1E88E5', '#8E24AA', '#EC407A'
  ]

  get printState (): string {
    return (this.$typedState.printer.printer as any).print_stats?.state ?? ''
  }

  get printing (): boolean {
    return this.printState === 'printing'
  }

  get canSelectTool (): boolean {
    return this.klippyReady
  }

  get canSend (): boolean {
    return this.klippyReady
  }

  // Service operations (unload, assist, reconnect) are blocked while printing
  get canService (): boolean {
    return this.klippyReady && !['printing', 'paused'].includes(this.printState)
  }

  get modelName (): string {
    for (let i = 0; i < this.aceInstanceCount; i++) {
      const m = this.aceInstance(i)?.model
      if (m) return m
    }
    return '—'
  }

  get firmwareVersion (): string {
    for (let i = 0; i < this.aceInstanceCount; i++) {
      const f = this.aceInstance(i)?.firmware
      if (f) return f
    }
    return '—'
  }

  get connectionState (): string {
    const inst: any = this.aceInstance(0)
    return String(inst?.connection_state ?? '')
  }

  get isConnected (): boolean {
    return this.connectionState === 'connected'
  }

  get currentToolColor (): string | undefined {
    if (this.currentTool < 0) return undefined
    const inst = Math.floor(this.currentTool / 4)
    const slot = this.currentTool % 4
    const s = this.slotsOf(inst)[slot]
    return this.slotEmpty(s) ? undefined : this.slotColorCss(s)
  }

  // Filament reached the internal ACE path (at least the first sensor)
  get pathInAce (): boolean {
    return this.rdmSensor === true || this.feederSensor === true || this.nozzleSensor === true
  }

  // --- Path node visibility: a node shows only when its sensor exists ---
  get showRdmNode (): boolean {
    return this.rdmSensor !== null
  }

  get showEncNode (): boolean {
    return this.feederSensor !== null
  }

  get showNozzleNode (): boolean {
    return this.nozzleSensor !== null
  }

  get showPath (): boolean {
    return this.showRdmNode || this.showEncNode || this.showNozzleNode
  }

  // Jam: filament is reported at the nozzle but the encoder does not see it.
  get encoderJam (): boolean {
    return this.feederSensor === false && this.nozzleSensor === true
  }

  // Filament positions along the path. The encoder cannot see filament
  // unless the splitter does (filament reaches the encoder only after the
  // splitter): when the splitter sensor reports empty, the encoder node
  // goes dark no matter what the encoder sensor itself says. When no
  // splitter sensor exists, the encoder sensor speaks for itself. A jam
  // (filament at the nozzle but not at the encoder) turns the encoder red.
  get filamentAtEncoder (): boolean {
    if (this.encoderJam) return false
    if (this.rdmSensor === null) return this.feederSensor === true
    return this.rdmSensor === true
  }

  get filamentAtNozzle (): boolean {
    return this.nozzleSensor === true
  }

  // Filament has passed the encoder region (encoder green or further).
  get filamentPastEncoder (): boolean {
    return this.filamentAtEncoder || this.filamentAtNozzle
  }

  // Segment flow animation while ACE is loading/unloading: the segment
  // between the last sensor that sees filament and the next one flows.
  // Works in both directions (load and unload) with the same rule.
  get flowToSplitter (): boolean {
    return this.aceBusy && this.pathInAce && this.rdmSensor !== true
  }

  get flowToEncoder (): boolean {
    if (!this.aceBusy || this.filamentAtEncoder) return false
    return this.rdmSensor === true || (this.rdmSensor === null && this.pathInAce)
  }

  get flowToNozzle (): boolean {
    return this.aceBusy && this.filamentAtEncoder && this.nozzleSensor !== true
  }

  // ACE is busy with a load/unload action: instance status is known and
  // not ready/idle. Unknown status = idle (lines stay solid, no errors).
  get aceBusy (): boolean {
    for (let i = 0; i < this.aceInstanceCount; i++) {
      const s = String(this.aceInstance(i)?.status ?? '').toLowerCase()
      if (s && !['ready', 'idle'].includes(s)) return true
    }
    return false
  }

  // Feed assist state: from the driver, local fallback until it reports
  get feedAssistOn (): boolean {
    const s = this.feedAssistSlot
    if (s == null) return this.faLocal ?? false
    return s >= 0
  }

  get dryerRunning (): boolean {
    return this.isDryerRunning(this.dryerInstance)
  }

  get materialItems (): string[] {
    const items = [...this.materials]
    const cur = this.slotDialog.material
    if (cur && !items.includes(cur)) items.unshift(cur)
    return items
  }

  get slotDialogCssColor (): string {
    const c = this.slotDialog.color
    return `rgb(${c[0]}, ${c[1]}, ${c[2]})`
  }

  chamberTemp (instanceIndex: number): number {
    return Math.round(Number(this.aceInstance(instanceIndex)?.temp ?? 0))
  }

  remainText (instanceIndex: number): string {
    // The driver reports dryer times in seconds.
    const s = Math.max(0, Math.round(this.dryerOf(instanceIndex).remain_time))
    if (s < 60 && s > 0) return `${s} s`
    return `${Math.round(s / 60)} min`
  }

  slotTool (slot: AceSlot): number {
    return typeof slot.tool === 'number' ? slot.tool : this.currentTool
  }

  slotStatusText (instanceIndex: number, slot: AceSlot): string {
    if (this.slotEmpty(slot)) return 'EMPTY'
    if (this.isCurrentTool(instanceIndex, slot.index) && this.printing) return 'PRINTING'
    if (this.isCurrentTool(instanceIndex, slot.index)) return 'ACTIVE'
    return (slot.status || 'READY').toUpperCase()
  }

  statusClass (instanceIndex: number, slot: AceSlot): string {
    if (this.slotEmpty(slot)) return 'ace-status-empty'
    if (this.isCurrentTool(instanceIndex, slot.index)) {
      return this.printing ? 'ace-status-printing' : 'ace-status-active'
    }
    return 'ace-status-ready'
  }

  // Spool ring keeps the real filament color; dark colors additionally
  // get a light outer ring (ace-spool-dark) so they stay visible on the
  // dark slot tile.
  spoolBorderCss (slot?: AceSlot): string | undefined {
    if (this.slotEmpty(slot)) return undefined
    return this.slotColorCss(slot)
  }

  slotDark (slot?: AceSlot): boolean {
    const c = slot?.color
    if (Array.isArray(c) && c.length >= 3) {
      const lum = 0.299 * Number(c[0]) + 0.587 * Number(c[1]) + 0.114 * Number(c[2])
      return lum < 60
    }
    return false
  }

  // Swatch border: dark swatches need a light border to stay visible.
  swatchBorder (css: string): string {
    const m = css.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i)
    if (!m) return 'rgba(0, 0, 0, 0.35)'
    const lum = 0.299 * parseInt(m[1], 16) + 0.587 * parseInt(m[2], 16) + 0.114 * parseInt(m[3], 16)
    return lum < 80 ? '#9E9E9E' : 'rgba(0, 0, 0, 0.35)'
  }

  spoolClasses (instanceIndex: number, slot: AceSlot): Record<string, boolean> {
    return {
      'ace-spool-empty': this.slotEmpty(slot),
      'ace-spool-dark': !this.slotEmpty(slot) && this.slotDark(slot),
      'ace-spool-current': this.isCurrentTool(instanceIndex, slot.index),
      'ace-spool-printing': this.isCurrentTool(instanceIndex, slot.index) && this.printing
    }
  }

  isCurrentTool (instanceIndex: number, slotIndex: number): boolean {
    return this.currentTool === this.toolIndex(instanceIndex, slotIndex)
  }

  async send (cmd: string) {
    if (this.busy) return
    this.busy = true
    try {
      await this.sendGcode(cmd)
    } finally {
      this.busy = false
    }
  }

  selectTool (tool: number) {
    this.send(`T${tool}`)
  }

  toggleFeedAssist (value: boolean) {
    if (!this.canService || this.currentTool < 0) return
    if (this.feedAssistSlot == null) this.faLocal = value
    this.send(value
      ? `ACE_ENABLE_FEED_ASSIST T=${this.currentTool}`
      : `ACE_DISABLE_FEED_ASSIST T=${this.currentTool}`)
  }

  // --- Slot dialog ---

  openSlotDialog (instanceIndex: number, slot: AceSlot) {
    const c = Array.isArray(slot.color) && slot.color.length >= 3 ? slot.color : [0, 0, 0]
    this.slotDialog = {
      open: true,
      instance: instanceIndex,
      slotIndex: slot.index,
      tool: typeof slot.tool === 'number' ? slot.tool : this.toolIndex(instanceIndex, slot.index),
      material: slot.material || 'PLA',
      color: [Number(c[0]) || 0, Number(c[1]) || 0, Number(c[2]) || 0]
    }
  }

  pickSwatch (css: string) {
    const m = css.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i)
    if (!m) return
    this.slotDialog.color = [
      parseInt(m[1], 16),
      parseInt(m[2], 16),
      parseInt(m[3], 16)
    ]
  }

  saveSlotDialog () {
    const [r, g, b] = this.slotDialog.color
    const cmd = `ACE_SET_SLOT T=${this.slotDialog.tool} MATERIAL=${this.slotDialog.material} COLOR=${r},${g},${b}`
    this.slotDialog.open = false
    this.send(cmd)
  }

  // --- Dryer dialog ---

  openDryerDialog () {
    const d = this.dryerOf(this.dryerInstance)
    if (d.target_temp >= 50) this.dryTemp = d.target_temp
    // Driver reports duration in seconds, slider works in minutes
    const durMin = Math.round(d.duration / 60)
    if (durMin >= 30) this.dryDuration = Math.min(240, durMin)
    this.dryerDialog = true
  }

  startDrying () {
    const params = [`TEMP=${this.dryTemp}`, `DURATION=${this.dryDuration}`]
    if (this.aceInstanceCount > 1) params.push(`INSTANCE=${this.dryerInstance}`)
    this.dryerDialog = false
    this.send(`ACE_START_DRYING ${params.join(' ')}`)
  }

  stopDrying () {
    const cmd = this.aceInstanceCount > 1
      ? `ACE_STOP_DRYING INSTANCE=${this.dryerInstance}`
      : 'ACE_STOP_DRYING'
    this.send(cmd)
  }
}
</script>

<style lang="scss" scoped>
.ace-disabled {
  pointer-events: none !important;
  opacity: 0.5 !important;
}

.smaller-font {
  font-size: 0.8em;
}

.ace-header {
  border-bottom: 1px solid rgba(128, 128, 128, 0.25);
  padding-bottom: 4px;
}

// --- Slots ---
.ace-slot-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  padding: 6px 4px 8px;
  background: #2c2c30;
  border: 1px solid #454549;
  border-radius: 10px;
}

.ace-slot-label {
  font-size: 0.75em;
  letter-spacing: 0.08em;
}

.ace-slot-status {
  font-size: 0.75em;
  font-weight: 600;
  letter-spacing: 0.08em;
  margin: 2px 0 6px;
}

.ace-status-ready {
  color: #4caf50;
}

.ace-status-active {
  color: #2196f3;
}

.ace-status-printing {
  color: #ff9800;
}

.ace-status-empty {
  color: rgba(128, 128, 128, 0.6);
}

.ace-spool-wrap {
  position: relative;
  line-height: 0;
}

.ace-spool {
  position: relative;
  width: 58px;
  height: 58px;
  border-radius: 50%;
  border: 10px solid rgba(128, 128, 128, 0.4);
  background: transparent;
  padding: 0;
  cursor: pointer;
  transition: box-shadow 0.2s ease;

  &:disabled {
    cursor: default;
  }
}

.ace-spool-inner {
  position: absolute;
  inset: 8px;
  border-radius: 50%;
  background: #1e1e20;
  border: 1px solid rgba(128, 128, 128, 0.35);
}

.ace-spool-empty {
  border-style: dashed;
  border-color: rgba(128, 128, 128, 0.35) !important;
  opacity: 0.6;
}

.ace-spool-current {
  box-shadow: 0 0 0 2px #2196f3, 0 0 8px rgba(33, 150, 243, 0.6);
}

.ace-spool-printing {
  box-shadow: 0 0 0 2px #ff9800, 0 0 10px rgba(255, 152, 0, 0.7);
}

// Dark filament colors (e.g. black): light outer ring for contrast
.ace-spool-dark {
  box-shadow: 0 0 0 2px #6e6e73;
}

.ace-spool-current.ace-spool-dark {
  box-shadow: 0 0 0 2px #2196f3, 0 0 8px rgba(33, 150, 243, 0.6);
}

.ace-spool-edit {
  position: absolute;
  right: -7px;
  bottom: -5px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 1px solid rgba(128, 128, 128, 0.4);
  background: #262629;
  color: #9e9e9e;
  padding: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;

  &:disabled {
    cursor: default;
    opacity: 0.5;
  }
}

.ace-slot-material {
  margin-top: 6px;
  font-size: 0.8em;
  line-height: 1.15;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ace-slot-sub {
  font-size: 0.75em;
  min-height: 1.2em;
}

// --- Slot color dialog ---
.ace-dlg-ring {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: 9px solid transparent;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.ace-dlg-ring-inner {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #262629;
  border: 1px solid rgba(128, 128, 128, 0.35);
}

.ace-swatch {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 1px solid rgba(0, 0, 0, 0.35);
  margin: 0 8px 8px 0;
  padding: 0;
  cursor: pointer;
}

// --- Dryer dialog ---
.ace-chamber-temp {
  font-size: 1.5em;
  font-weight: 600;
  line-height: 1.2;
}

.ace-dlg-label {
  font-size: 0.72em;
  letter-spacing: 0.08em;
}

.ace-dlg-slider-label {
  width: 92px;
  flex-shrink: 0;
  font-size: 0.85em;
  color: #9e9e9e;
}

.ace-dlg-slider-val {
  width: 48px;
  flex-shrink: 0;
  text-align: right;
  font-size: 0.85em;
  font-weight: 600;
}

// --- Feed assist segmented switch ---
.ace-fa-switch {
  display: flex;
  width: 100%;
  height: 28px;
  border: 1px solid #4a4a4f;
  border-radius: 4px;
  overflow: hidden;
  cursor: pointer;

  // Blue frame only while feed assist is on
  &.ace-fa-enabled {
    border-color: #2196f3;
  }

  &.ace-fa-disabled {
    opacity: 0.5;
    cursor: default;
  }
}

.ace-fa-seg {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.72em;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: #9e9e9e;
  background: rgba(33, 150, 243, 0.08);
  padding: 0 2px;
  white-space: nowrap;
  user-select: none;

  &.active {
    color: #ffffff;
    font-weight: 700;
  }

  // OFF is the left segment: grey when active. FEED ASSIST is the right
  // one: primary blue when active.
  &:first-child.active {
    background: #4a4a4f;
  }

  &:last-child.active {
    background: #2196f3;
  }
}

// --- Filament path ---
.ace-path {
  display: flex;
  align-items: center;
  padding: 4px 8px 0;
}

.ace-path-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 44px;
}

.ace-path-dot {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: rgba(128, 128, 128, 0.35);
  border: 2px solid rgba(128, 128, 128, 0.5);
  transition: background 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease;

  .active > & {
    background: #4caf50;
    border-color: #4caf50;
    box-shadow: 0 0 6px rgba(76, 175, 80, 0.8);
  }

  // Sensor present but no filament: hollow red circle
  .empty > & {
    background: transparent;
    border-color: #e53935;
    box-shadow: 0 0 6px rgba(229, 57, 53, 0.5);
  }
}

.ace-path-caption {
  margin-top: 3px;
  font-size: 0.68em;
  color: rgba(128, 128, 128, 0.8);
  white-space: nowrap;
}

// Encoder: grey when empty, green when filament reaches it (which
// requires a full splitter), red on jam (filament at the nozzle but not
// at the encoder)
.ace-enc-wheel {
  position: relative;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 2px solid rgba(128, 128, 128, 0.5);
  background: rgba(128, 128, 128, 0.15);
  transition: border-color 0.3s ease, background 0.3s ease;

  &.active {
    border-color: #4caf50;
    background: rgba(76, 175, 80, 0.15);
  }

  &.jam {
    border-color: #e53935;
    background: rgba(229, 57, 53, 0.15);
    box-shadow: 0 0 6px rgba(229, 57, 53, 0.6);
  }
}

.ace-enc-spoke {
  position: absolute;
  left: 50%;
  top: 1px;
  width: 2px;
  height: 7px;
  margin-left: -1px;
  border-radius: 1px;
  background: rgba(128, 128, 128, 0.7);

  .active > & {
    background: #4caf50;
  }

  .jam & {
    background: #e53935;
  }
}

.ace-path-line {
  position: relative;
  flex: 1;
  height: 3px;
  margin: 0 4px 14px;
  background: rgba(128, 128, 128, 0.35);
  border-radius: 2px;
  overflow: hidden;
}

.ace-path-flow {
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
}

// Solid green while filament is present; the flowing animation only
// runs while ACE is actually loading/unloading (aceBusy).
.ace-path-line.active {
  background: rgba(76, 175, 80, 0.55);

  &.busy {
    background: rgba(76, 175, 80, 0.3);

    .ace-path-flow {
      background: linear-gradient(90deg, transparent, #4caf50 50%, transparent);
      animation: ace-flow 1.6s linear infinite;
    }
  }
}

@keyframes ace-flow {
  from { transform: translateX(-100%); }
  to { transform: translateX(100%); }
}
</style>
