// ValgACE Dashboard JavaScript

const { createApp } = Vue;

createApp({
    data() {
        return {
            currentLanguage: 'en',
            translations: {
                en: {
                    header: {
                        title: 'ACE Control Panel',
                        connectionLabel: 'Status',
                        connected: 'Connected',
                        disconnected: 'Disconnected',
                        instance: 'ACE Instance:',
                        instanceLabel: 'ACE {index}'
                    },
                    cards: {
                        deviceStatus: 'Device Status',
                        dryer: 'Dryer Control',
                        slots: 'Filament Slots',
                        quickActions: 'Quick Actions'
                    },
                    deviceInfo: {
                        model: 'Model',
                        firmware: 'Firmware',
                        bootFirmware: 'Boot Firmware',
                        status: 'Status',
                        temp: 'Temperature',
                        fan: 'Fan Speed',
                        usb: 'USB',
                        rfid: 'RFID',
                        rfidOn: 'Enabled',
                        rfidOff: 'Disabled'
                    },
                    dryer: {
                        status: 'Status',
                        targetTemp: 'Target Temperature',
                        duration: 'Set Duration',
                        remainingTime: 'Remaining Time',
                        currentTemperature: 'Current Temperature',
                        inputs: {
                            temp: 'Temperature (°C):',
                            duration: 'Duration (min):'
                        },
                        buttons: {
                            start: 'Start Drying',
                            stop: 'Stop Drying'
                        }
                    },
                    slots: {
                        slot: 'Slot',
                        status: 'Status',
                        type: 'Type',
                        temp: 'Temp',
                        sku: 'SKU',
                        rfid: 'RFID'
                    },
                    quickActions: {
                        unload: 'Save Inventory',
                        stopAssist: 'Stop All Assist',
                        refresh: 'Refresh Status'
                    },
                    buttons: {
                        load: 'Load',
                        unload: 'Unload',
                        assistOn: 'Assist ON',
                        assistOff: 'Assist OFF'
                    },
                    dialogs: {
                        feedTitle: 'Feed Filament - Slot {slot}',
                        retractTitle: 'Retract Filament - Slot {slot}',
                        length: 'Length (mm):',
                        speed: 'Speed (mm/s):',
                        execute: 'Execute',
                        cancel: 'Cancel'
                    },
                    notifications: {
                        websocketConnected: 'WebSocket connected',
                        websocketDisconnected: 'WebSocket disconnected',
                        apiError: 'API error: {error}',
                        loadError: 'Status load error: {error}',
                        commandSuccess: 'Command {command} executed successfully',
                        commandSent: 'Command {command} sent',
                        commandError: 'Error: {error}',
                        commandErrorGeneric: 'Command execution error',
                        executeError: 'Command execution error: {error}',
                        feedAssistOn: 'Feed assist enabled for slot {index}',
                        feedAssistOff: 'Feed assist disabled for slot {index}',
                        feedAssistAllOff: 'Feed assist disabled for all slots',
                        feedAssistAllOffError: 'Failed to disable feed assist',
                        refreshStatus: 'Status refreshed',
                        validation: {
                            tempRange: 'Temperature must be between 20 and 55°C',
                            durationMin: 'Duration must be at least 1 minute',
                            feedLength: 'Length must be at least 1 mm',
                            retractLength: 'Length must be at least 1 mm'
                        }
                    },
                    statusMap: {
                        ready: 'Ready',
                        busy: 'Busy',
                        unknown: 'Unknown',
                        disconnected: 'Disconnected'
                    },
                    dryerStatusMap: {
                        drying: 'Drying',
                        stop: 'Stopped'
                    },
                    connectionStateMap: {
                        connected: 'Connected',
                        reconnecting: 'Reconnecting',
                        connecting: 'Connecting',
                        initializing: 'Initializing',
                        disabled: 'Disabled',
                        disconnected: 'Disconnected',
                        unknown: 'Unknown'
                    },
                    slotStatusMap: {
                        ready: 'Ready',
                        empty: 'Empty',
                        busy: 'Busy',
                        unknown: 'Unknown'
                    },
                    rfidStatusMap: {
                        0: 'Not found',
                        1: 'Error',
                        2: 'Identified',
                        3: 'Identifying...'
                    },
                    common: {
                        unknown: 'Unknown'
                    },
                    time: {
                        hours: 'h',
                        minutes: 'min',
                        minutesShort: 'm',
                        secondsShort: 's'
                    }
                }
            },
            // Connection
            wsConnected: false,
            ws: null,
            apiBase: ACE_DASHBOARD_CONFIG?.apiBase || window.location.origin,
            
            // Device Status
            deviceStatus: {
                status: 'unknown',
                connection_state: 'unknown',
                model: 'Anycubic Color Engine Pro',
                firmware: 'N/A',
                boot_firmware: 'N/A',
                temp: 0,
                humidity: null,
                fan_speed: 0,
                enable_rfid: 0,
                usb_port: '',
                usb_path: ''
            },
            
            // Dryer
            dryerStatus: {
                status: 'stop',
                target_temp: 0,
                duration: 0,
                remain_time: 0
            },
            dryingTemp: ACE_DASHBOARD_CONFIG?.defaults?.dryingTemp || 50,
            dryingDuration: ACE_DASHBOARD_CONFIG?.defaults?.dryingDuration || 240,
            
            // Slots
            slots: [],
            currentTool: -1,
            feedAssistSlot: -1,  // Индекс слота с активным feed assist (-1 = выключен)
            instanceOptions: [],
            selectedInstance: 0,
            statusRequestSeq: 0,
            instancesPanels: [],
            colorPresets: ['#ff0000', '#00ff00', '#0000ff', '#ff9900', '#ffff00', '#ff00ff', '#00ffff', '#ffffff', '#808080', '#000000'],
            colorPickerTarget: null,
            materialOptions: {
                'PLA': 200,
                'PETG': 235,
                'ABS': 240,
                'ASA': 245,
                'PVA': 185,
                'HIPS': 230,
                'PC': 260,
                'PLA+': 210,
                'PLA Glow': 210,
                'PLA High Speed': 215,
                'PLA Marble': 205,
                'PLA Matte': 205,
                'PLA SE': 210,
                'PLA Silk': 215,
                'TPU': 210
            },
            rfidSyncEnabled: false,
            editingHex: {},
            
            // Modals
            showFeedModal: false,
            showRetractModal: false,
            feedSlot: 0,
            feedLength: ACE_DASHBOARD_CONFIG?.defaults?.feedLength || 50,
            feedSpeed: ACE_DASHBOARD_CONFIG?.defaults?.feedSpeed || 25,
            retractSlot: 0,
            retractLength: ACE_DASHBOARD_CONFIG?.defaults?.retractLength || 50,
            retractSpeed: ACE_DASHBOARD_CONFIG?.defaults?.retractSpeed || 25,
            
            // Notifications
            notification: {
                show: false,
                message: '',
                type: 'info'
            }
        };
    },
    
    mounted() {
        this.connectWebSocket();
        this.loadStatus();
        this.updateDocumentTitle();
        
            // Auto-refresh
        const refreshInterval = ACE_DASHBOARD_CONFIG?.autoRefreshInterval || 5000;
        setInterval(() => {
            if (this.wsConnected) {
                this.loadStatus();
            }
        }, refreshInterval);
    },
    
    methods: {
        t(path, params = {}) {
            const keys = path.split('.');
            let value = this.translations[this.currentLanguage];
            for (const key of keys) {
                if (value && Object.prototype.hasOwnProperty.call(value, key)) {
                    value = value[key];
                } else {
                    return undefined;
                }
            }
            if (typeof value === 'string') {
                return value.replace(/\{(\w+)\}/g, (match, token) => {
                    return Object.prototype.hasOwnProperty.call(params, token) ? params[token] : match;
                });
            }
            return undefined;
        },

        isSlotHexEditing(instIndex, slotIndex) {
            const key = `${instIndex}-${slotIndex}`;
            return !!this.editingHex[key];
        },

        getPreviousHex(prevPanel, slotIndex) {
            if (!prevPanel || !Array.isArray(prevPanel.slots)) return null;
            const found = prevPanel.slots.find(s => s.index === slotIndex);
            return found ? found.hex : null;
        },

        startHexEdit(instIndex, slotIndex) {
            const key = `${instIndex}-${slotIndex}`;
            const next = { ...this.editingHex };
            next[key] = true;
            this.editingHex = next;
        },

        commitSlotHex(slot, instanceIndex) {
            if (!slot) return;
            const val = slot.hex || '';
            const hex = (typeof val === 'string' && /^#?[0-9a-fA-F]{6}$/.test(val.trim())) ? (val.trim().startsWith('#') ? val.trim() : `#${val.trim()}`) : null;
            if (!hex) {
                this.showNotification('Invalid hex color', 'error');
                return;
            }
            const normalized = hex.trim().startsWith('#') ? hex.trim() : `#${hex.trim()}`;
            slot.hex = normalized;
            this.setSlotColor(slot.index, instanceIndex, normalized, slot.tool, slot.material || slot.type || '', slot.temp);
            const key = `${instanceIndex}-${slot.index}`;
            const next = { ...this.editingHex };
            delete next[key];
            this.editingHex = next;
        },
        getInstanceFeedAssistSlot(instanceIndex) {
            const panel = this.instancesPanels.find(p => p.index === instanceIndex);
            return (panel && typeof panel.feedAssistSlot === 'number') ? panel.feedAssistSlot : -1;
        },
        setInstanceFeedAssistSlot(instanceIndex, slotIndex) {
            this.instancesPanels = this.instancesPanels.map(panel => {
                if (panel.index !== instanceIndex) {
                    return panel;
                }
                return { ...panel, feedAssistSlot: slotIndex };
            });
            if (instanceIndex === this.selectedInstance) {
                this.feedAssistSlot = slotIndex;
            }
        },
        isSlotLocked(inst, slot) {
            const instIndex = typeof inst === 'number' ? inst : inst?.index;
            const panel = this.instancesPanels.find(p => p.index === instIndex);
            const syncEnabled = panel ? panel.rfidSyncEnabled : this.rfidSyncEnabled;
            return !!(syncEnabled && slot && slot.rfid === 2);
        },

        updateDocumentTitle() {
            document.title = this.t('header.title');
        },

        // WebSocket Connection
        connectWebSocket() {
            const wsUrl = getWebSocketUrl();
            
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                this.wsConnected = true;
                this.showNotification(this.t('notifications.websocketConnected'), 'success');
                this.subscribeToStatus();
            };
            
            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (e) {
                    console.error('Error parsing WebSocket message:', e);
                }
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.wsConnected = false;
            };
            
            this.ws.onclose = () => {
                this.wsConnected = false;
                this.showNotification(this.t('notifications.websocketDisconnected'), 'error');
                // Reconnect after configured timeout
                const reconnectTimeout = ACE_DASHBOARD_CONFIG?.wsReconnectTimeout || 3000;
                setTimeout(() => this.connectWebSocket(), reconnectTimeout);
            };
        },
        
        subscribeToStatus() {
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
            
            this.ws.send(JSON.stringify({
                jsonrpc: "2.0",
                method: "printer.objects.subscribe",
                params: {
                    objects: {
                        "ace": null
                    }
                },
                id: 5434
            }));
        },
        
        handleWebSocketMessage(data) {
            if (data.method === "notify_status_update") {
                const aceData = data.params[0]?.ace;
                if (aceData && typeof aceData === 'object') {
                    const wsInstance = Number(aceData.instance_index);
                    if (!Number.isInteger(wsInstance)) {
                        // Manager-level push updates are not instance-scoped.
                        // Apply only global fields that are safe across instances.
                        const globalUpdate = {};
                        if (aceData.current_index !== undefined) {
                            globalUpdate.current_index = aceData.current_index;
                        }
                        if (typeof aceData.rfid_sync_enabled === 'boolean') {
                            globalUpdate.rfid_sync_enabled = aceData.rfid_sync_enabled;
                        }
                        if (Object.keys(globalUpdate).length > 0) {
                            this.updateStatus(globalUpdate);
                        }
                        return;
                    }
                    if (wsInstance !== this.selectedInstance) {
                        // Ignore updates for other instances; periodic polling will fetch selected instance
                        return;
                    }
                    this.updateStatus(aceData);
                }
            }
        },
        
        // API Calls
        async loadStatus() {
            try {
                const inst = Number.isInteger(this.selectedInstance) ? this.selectedInstance : 0;
                const requestSeq = ++this.statusRequestSeq;
                const requestInstance = inst;
                const response = await fetch(`${this.apiBase}/server/ace/status?instance=${inst}`);
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                const result = await response.json();
                
                if (ACE_DASHBOARD_CONFIG?.debug) {
                    console.log('Status response:', result);
                }
                
                if (result.error) {
                    console.error('API error:', result.error);
                    this.showNotification(this.t('notifications.apiError', { error: result.error }), 'error');
                    return;
                }
                
                // API может возвращать данные напрямую или в result.result
                // Обрабатываем оба случая
                const statusData = result.result || result;

                // Ignore stale responses from older requests.
                if (requestSeq !== this.statusRequestSeq) {
                    return;
                }

                // Ignore response if user changed selected instance while request was in flight.
                if (this.selectedInstance !== requestInstance) {
                    return;
                }

                // Reject mismatched instance payloads to prevent cross-instance display bleed-through.
                if (
                    Number.isInteger(requestInstance) &&
                    typeof statusData?.instance_index === 'number' &&
                    statusData.instance_index !== requestInstance
                ) {
                    console.warn('Ignoring mismatched ACE status payload:', {
                        requested: requestInstance,
                        received: statusData.instance_index
                    });
                    return;
                }
                
                // Проверяем, что это действительно данные статуса (есть хотя бы одно из полей)
                if (statusData && typeof statusData === 'object' && 
                    (statusData.status !== undefined || statusData.slots !== undefined || statusData.dryer !== undefined)) {
                    this.updateStatus(statusData);
                } else {
                    console.warn('Invalid status data in response:', result);
                }
            } catch (error) {
                console.error('Error loading status:', error);
                this.showNotification(this.t('notifications.loadError', { error: error.message }), 'error');
            }
        },
        
        updateStatus(data) {
            if (!data || typeof data !== 'object') {
                console.warn('Invalid status data:', data);
                return;
            }

            // Sync active global tool from manager status when provided.
            // Accept both top-level current_index and nested ace_manager.current_index.
            let incomingCurrentTool = null;
            const topLevelCurrent = Number(data.current_index);
            const managerCurrent = Number(data?.ace_manager?.current_index);
            if (Number.isInteger(topLevelCurrent)) {
                incomingCurrentTool = topLevelCurrent;
            } else if (Number.isInteger(managerCurrent)) {
                incomingCurrentTool = managerCurrent;
            }
            if (incomingCurrentTool !== null) {
                this.currentTool = incomingCurrentTool;
            }
            
            if (typeof data.rfid_sync_enabled === 'boolean') {
                this.rfidSyncEnabled = data.rfid_sync_enabled;
            }
            
            if (ACE_DASHBOARD_CONFIG?.debug) {
                console.log('Updating status with data:', data);
            }
            
            // Track instances list
            const instances = Array.isArray(data.instances) ? data.instances : null;
            if (instances) {
                this.instanceOptions = instances
                    .map(item => ({ index: typeof item?.index === 'number' ? item.index : 0 }))
                    .sort((a, b) => a.index - b.index);
                if (typeof data.instance_index === 'number') {
                    if (
                        !Number.isInteger(this.selectedInstance) ||
                        !this.instanceOptions.find(opt => opt.index === this.selectedInstance)
                    ) {
                        this.selectedInstance = data.instance_index;
                    }
                }
            }
            
            // Обновляем статус устройства (обновляем только если поля присутствуют)
            if (data.status !== undefined) {
                this.deviceStatus.status = data.status;
            }
            if (data.connection_state !== undefined) {
                this.deviceStatus.connection_state = data.connection_state || 'unknown';
            }
            if (data.model !== undefined) {
                this.deviceStatus.model = data.model;
            }
            if (data.firmware !== undefined) {
                this.deviceStatus.firmware = data.firmware;
            }
            if (data.boot_firmware !== undefined) {
                this.deviceStatus.boot_firmware = data.boot_firmware;
            }
            if (data.temp !== undefined) {
                this.deviceStatus.temp = data.temp;
            }
            if (data.humidity !== undefined) {
                this.deviceStatus.humidity = data.humidity;
            } else {
                const hasProtocol = data.protocol !== undefined && data.protocol !== null;
                const protocolName = hasProtocol ? String(data.protocol).toLowerCase() : '';
                // Only clear when payload explicitly identifies a non-ACE2 protocol.
                // Partial updates may omit protocol and humidity, and should not wipe
                // a previously displayed ACE2 humidity value.
                if (hasProtocol && protocolName !== 'ace2_proto') {
                    this.deviceStatus.humidity = null;
                }
            }
            if (data.fan_speed !== undefined) {
                this.deviceStatus.fan_speed = data.fan_speed;
            }
            if (data.usb_port !== undefined) {
                this.deviceStatus.usb_port = data.usb_port;
            }
            if (data.usb_path !== undefined) {
                this.deviceStatus.usb_path = data.usb_path;
            }
            if (data.enable_rfid !== undefined) {
                this.deviceStatus.enable_rfid = data.enable_rfid;
            }
            
            // Обновляем статус сушилки
            const dryer = data.dryer || data.dryer_status;
            
            if (dryer && typeof dryer === 'object') {
                // duration всегда в минутах (данные из ace.py уже нормализованы)
                if (dryer.duration !== undefined) {
                    this.dryerStatus.duration = Math.floor(dryer.duration); // Целое число минут
                }
                
                // remain_time: данные из ace.py уже конвертированы из секунд в минуты
                // Но на всякий случай проверяем: если значение слишком большое (> 1440 минут = 24 часа),
                // значит оно не было конвертировано и все еще в секундах
                if (dryer.remain_time !== undefined) {
                    let remain_time = dryer.remain_time;
                    
                    // Если remain_time > 1440 (24 часа в минутах), это точно секунды, конвертируем
                    if (remain_time > 1440) {
                        remain_time = remain_time / 60;
                    }
                    // Также проверяем: если remain_time значительно больше duration (в минутах), 
                    // и значение > 60, вероятно это секунды
                    else if (this.dryerStatus.duration > 0 && remain_time > this.dryerStatus.duration * 1.5 && remain_time > 60) {
                        remain_time = remain_time / 60;
                    }
                    
                    this.dryerStatus.remain_time = remain_time; // Может быть дробным (минуты.секунды)
                }
                if (dryer.status !== undefined) {
                    this.dryerStatus.status = dryer.status;
                }
                if (dryer.target_temp !== undefined) {
                    this.dryerStatus.target_temp = dryer.target_temp;
                }
            }
            
            // Обновляем слоты и панели инстансов
            const instancesRaw = Array.isArray(data.instances) ? data.instances : [];
            const prevPanels = this.instancesPanels || [];
            if (instancesRaw.length > 0) {
                this.instancesPanels = instancesRaw.map(item => {
                    const slotsArr = Array.isArray(item.slots) ? item.slots : [];
                    const prevPanel = prevPanels.find(p => p.index === item.index);
                    return {
                        index: typeof item.index === 'number' ? item.index : 0,
                        slots: slotsArr.map(slot => ({
                            index: slot.index !== undefined ? slot.index : -1,
                            tool: slot.tool !== undefined ? slot.tool : null,
                            status: slot.status || 'unknown',
                            type: slot.type || slot.material || '',
                            material: slot.material || slot.type || '',
                            temp: typeof slot.temp === 'number' ? slot.temp : 0,
                            color: Array.isArray(slot.color) ? slot.color : [0, 0, 0],
                            hex: this.isSlotHexEditing(item.index, slot.index)
                                ? this.getPreviousHex(prevPanel, slot.index) || this.getColorHex(slot.color)
                                : this.getColorHex(slot.color),
                            sku: slot.sku || '',
                            rfid: slot.rfid !== undefined ? slot.rfid : 0
                        })),
                        feedAssistSlot: typeof item.feed_assist_slot === 'number' ? item.feed_assist_slot : -1,
                        rfidSyncEnabled: typeof item.rfid_sync_enabled === 'boolean' ? item.rfid_sync_enabled : this.rfidSyncEnabled
                    };
                });
                const selectedPanel = this.instancesPanels.find(p => p.index === this.selectedInstance) || this.instancesPanels[0];
                if (selectedPanel) {
                    this.slots = selectedPanel.slots;
                    this.feedAssistSlot = selectedPanel.feedAssistSlot ?? -1;
                }
            } else if (data.slots !== undefined) {
                if (Array.isArray(data.slots)) {
                    this.slots = data.slots.map(slot => ({
                        index: slot.index !== undefined ? slot.index : -1,
                        tool: slot.tool !== undefined ? slot.tool : null,
                        status: slot.status || 'unknown',
                        type: slot.type || slot.material || '',
                        material: slot.material || slot.type || '',
                        color: Array.isArray(slot.color) ? slot.color : [0, 0, 0],
                        hex: this.isSlotHexEditing(this.selectedInstance, slot.index)
                            ? (this.slots.find(s => s.index === slot.index)?.hex || this.getColorHex(slot.color))
                            : this.getColorHex(slot.color),
                        temp: typeof slot.temp === 'number' ? slot.temp : 0,
                        sku: slot.sku || '',
                        rfid: slot.rfid !== undefined ? slot.rfid : 0
                    }));
                } else {
                    console.warn('Slots data is not an array:', data.slots);
                }
                if (data.feed_assist_slot !== undefined) {
                    this.feedAssistSlot = data.feed_assist_slot;
                } else if (data.feed_assist_count !== undefined && data.feed_assist_count > 0) {
                    if (this.feedAssistSlot === -1 && this.currentTool !== -1 && this.currentTool < 4) {
                        this.feedAssistSlot = this.currentTool;
                    }
                } else {
                    this.feedAssistSlot = -1;
                }
            }
            
            if (ACE_DASHBOARD_CONFIG?.debug) {
                console.log('Status updated:', {
                    deviceStatus: this.deviceStatus,
                    dryerStatus: this.dryerStatus,
                    slotsCount: this.slots.length,
                    feedAssistSlot: this.feedAssistSlot
                });
            }
        },
        
        onInstanceChange() {
            this.deviceStatus.humidity = null;
            this.loadStatus();
        },
        
        async executeCommand(command, params = {}) {
            try {
                const cmdParams = { ...params };
                // Inject selected instance if not provided
                if (typeof cmdParams.INSTANCE === 'undefined' && Number.isInteger(this.selectedInstance)) {
                    cmdParams.INSTANCE = this.selectedInstance;
                }

                const response = await fetch(`${this.apiBase}/server/ace/command`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        command: command,
                        params: cmdParams
                    })
                });
                
                const result = await response.json();
                
                if (ACE_DASHBOARD_CONFIG?.debug) {
                    console.log('Command response:', result);
                }
                
                if (result.error) {
                    this.showNotification(this.t('notifications.apiError', { error: result.error }), 'error');
                    return false;
                }
                
                if (result.result) {
                    if (result.result.success !== false && !result.result.error) {
                        this.showNotification(this.t('notifications.commandSuccess', { command }), 'success');
                        // Reload status after command
                        setTimeout(() => this.loadStatus(), 1000);
                        return true;
                    } else {
                        const errorMsg = result.result.error || result.result.message || this.t('notifications.commandErrorGeneric');
                        this.showNotification(this.t('notifications.commandError', { error: errorMsg }), 'error');
                        return false;
                    }
                }
                
                // Если нет result, но и нет ошибки - считаем успехом
                this.showNotification(this.t('notifications.commandSent', { command }), 'success');
                setTimeout(() => this.loadStatus(), 1000);
                return true;
            } catch (error) {
                console.error('Error executing command:', error);
                this.showNotification(this.t('notifications.executeError', { error: error.message }), 'error');
                return false;
            }
        },
        
        // Device Actions
        async changeToolForInstance(tool, instanceIndex) {
            const success = await this.executeCommand('ACE_CHANGE_TOOL', { TOOL: tool, INSTANCE: instanceIndex });
            if (success && instanceIndex === this.selectedInstance) {
                this.currentTool = tool;
            }
        },
        
        async unloadFilament(instanceIndex) {
            await this.changeToolForInstance(-1, instanceIndex);
        },

        async stopAssist() {
            let anySuccess = false;
            const instances = this.instanceOptions.length > 0 ? this.instanceOptions : [{ index: this.selectedInstance || 0 }];
            for (const inst of instances) {
                const activeSlot = this.getInstanceFeedAssistSlot(inst.index);
                if (activeSlot !== -1) {
                    const success = await this.disableFeedAssist(activeSlot, inst.index, true);
                    if (success) {
                        anySuccess = true;
                    }
                    continue;
                }

                // Fallback: try to disable all slots if we don't know which one is active
                for (let index = 0; index < 4; index++) {
                    const success = await this.executeCommand('ACE_DISABLE_FEED_ASSIST', { INDEX: index, INSTANCE: inst.index });
                    if (success) {
                        anySuccess = true;
                        this.setInstanceFeedAssistSlot(inst.index, -1);
                    }
                }
            }
            if (anySuccess) {
                this.instancesPanels = this.instancesPanels.map(panel => ({ ...panel, feedAssistSlot: -1 }));
                this.feedAssistSlot = -1;
                this.showNotification(this.t('notifications.feedAssistAllOff'), 'success');
            } else {
                this.showNotification(this.t('notifications.feedAssistAllOffError'), 'error');
            }
        },

        async saveInventoryAll() {
            let anySuccess = false;
            const instances = this.instanceOptions.length > 0 ? this.instanceOptions : [{ index: this.selectedInstance || 0 }];
            for (const inst of instances) {
                const success = await this.executeCommand('ACE_SAVE_INVENTORY', { INSTANCE: inst.index });
                if (success) {
                    anySuccess = true;
                }
            }
            if (anySuccess) {
                this.showNotification(this.t('notifications.commandSuccess', { command: 'ACE_SAVE_INVENTORY' }), 'success');
            } else {
                this.showNotification(this.t('notifications.commandErrorGeneric'), 'error');
            }
        },
        
        // Feed Assist Actions
        async toggleFeedAssist(index, instanceIndex) {
            const targetInstance = Number.isInteger(instanceIndex) ? instanceIndex : (this.selectedInstance || 0);
            const activeSlot = this.getInstanceFeedAssistSlot(targetInstance);
            if (activeSlot === index) {
                await this.disableFeedAssist(index, targetInstance);
            } else {
                if (activeSlot !== -1) {
                    await this.disableFeedAssist(activeSlot, targetInstance, true);
                }
                await this.enableFeedAssist(index, targetInstance);
            }
        },
        
        async enableFeedAssist(index, instanceIndex) {
            const targetInstance = Number.isInteger(instanceIndex) ? instanceIndex : (this.selectedInstance || 0);
            const activeSlot = this.getInstanceFeedAssistSlot(targetInstance);
            if (activeSlot !== -1 && activeSlot !== index) {
                await this.disableFeedAssist(activeSlot, targetInstance, true);
            }
            const success = await this.executeCommand('ACE_ENABLE_FEED_ASSIST', { INDEX: index, INSTANCE: targetInstance });
            if (success) {
                this.setInstanceFeedAssistSlot(targetInstance, index);
                this.showNotification(this.t('notifications.feedAssistOn', { index }), 'success');
            }
            return success;
        },
        
        async disableFeedAssist(index, instanceIndex, silent = false) {
            const targetInstance = Number.isInteger(instanceIndex) ? instanceIndex : (this.selectedInstance || 0);
            const success = await this.executeCommand('ACE_DISABLE_FEED_ASSIST', { INDEX: index, INSTANCE: targetInstance });
            if (success) {
                this.setInstanceFeedAssistSlot(targetInstance, -1);
                if (!silent) {
                    this.showNotification(this.t('notifications.feedAssistOff', { index }), 'success');
                }
            }
            return success;
        },
        
        // Dryer Actions
        async startDrying() {
            if (this.dryingTemp < 20 || this.dryingTemp > 65) {
                this.showNotification(this.t('notifications.validation.tempRange'), 'error');
                return;
            }
            
            if (this.dryingDuration < 1) {
                this.showNotification(this.t('notifications.validation.durationMin'), 'error');
                return;
            }
            
            await this.executeCommand('ACE_START_DRYING', {
                TEMP: this.dryingTemp,
                DURATION: this.dryingDuration,
                INSTANCE: this.selectedInstance
            });
        },
        
        async stopDrying() {
            await this.executeCommand('ACE_STOP_DRYING', { INSTANCE: this.selectedInstance });
        },
        
        // Feed/Retract Actions
        showFeedDialog(slot) {
            this.feedSlot = slot;
            this.feedLength = ACE_DASHBOARD_CONFIG?.defaults?.feedLength || 50;
            this.feedSpeed = ACE_DASHBOARD_CONFIG?.defaults?.feedSpeed || 25;
            this.showFeedModal = true;
        },
        
        closeFeedDialog() {
            this.showFeedModal = false;
        },
        
        async executeFeed() {
            if (this.feedLength < 1) {
                this.showNotification(this.t('notifications.validation.feedLength'), 'error');
                return;
            }
            
            const success = await this.executeCommand('ACE_FEED', {
                INDEX: this.feedSlot,
                LENGTH: this.feedLength,
                SPEED: this.feedSpeed
            });
            
            if (success) {
                this.closeFeedDialog();
            }
        },
        
        showRetractDialog(slot) {
            this.retractSlot = slot;
            this.retractLength = ACE_DASHBOARD_CONFIG?.defaults?.retractLength || 50;
            this.retractSpeed = ACE_DASHBOARD_CONFIG?.defaults?.retractSpeed || 25;
            this.showRetractModal = true;
        },
        
        closeRetractDialog() {
            this.showRetractModal = false;
        },
        
        async executeRetract() {
            if (this.retractLength < 1) {
                this.showNotification(this.t('notifications.validation.retractLength'), 'error');
                return;
            }
            
            const success = await this.executeCommand('ACE_RETRACT', {
                INDEX: this.retractSlot,
                LENGTH: this.retractLength,
                SPEED: this.retractSpeed
            });
            
            if (success) {
                this.closeRetractDialog();
            }
        },
        
        async refreshStatus() {
            await this.loadStatus();
            this.showNotification(this.t('notifications.refreshStatus'), 'success');
        },
        
        // Utility Functions
        getStatusText(status) {
            return this.t(`statusMap.${status}`) || status;
        },
        
        getConnectionStateText(state) {
            const key = state || 'unknown';
            return this.t(`connectionStateMap.${key}`) || this.t('common.unknown');
        },
        
        connectionBadgeClass() {
            if (!this.wsConnected) return 'disconnected';
            return this.deviceStatus.connection_state || 'unknown';
        },
        
        getDryerStatusText(status) {
            return this.t(`dryerStatusMap.${status}`) || status;
        },
        
        getSlotStatusText(status) {
            return this.t(`slotStatusMap.${status}`) || status;
        },
        
        getRfidStatusText(rfid) {
            // API sometimes returns boolean/null; normalize to numeric codes used in translations
            let key;
            if (rfid === true) key = 2;         // treat true as identified
            else if (rfid === false || rfid === null || rfid === undefined) key = 0; // not found
            else key = rfid;
            const value = this.t(`rfidStatusMap.${key}`);
            return value === undefined ? this.t('common.unknown') : value;
        },

        isRfidIdentified(rfid) {
            if (rfid === true) return true;
            if (rfid === false || rfid === null || rfid === undefined) return false;
            if (typeof rfid === 'number') return rfid === 2;
            const str = String(rfid).toLowerCase();
            return str === '2' || str === 'identified';
        },
        
        formatUsbPath(port, usbPath) {
            let displayPort = port;
            if (displayPort && displayPort.includes('USB_Single_Serial_')) {
                displayPort = displayPort.replace(/(USB_Single_Serial_)[A-Z0-9]+(-if)/i, '$1***$2');
            }
            if (displayPort && usbPath) {
                return `${displayPort} (${usbPath})`;
            }
            if (displayPort) return displayPort;
            if (usbPath) return usbPath;
            return this.t('common.unknown');
        },
        
        getColorHex(color) {
            if (!color || !Array.isArray(color) || color.length < 3) {
                return '#000000';
            }
            const r = Math.max(0, Math.min(255, color[0])).toString(16).padStart(2, '0');
            const g = Math.max(0, Math.min(255, color[1])).toString(16).padStart(2, '0');
            const b = Math.max(0, Math.min(255, color[2])).toString(16).padStart(2, '0');
            return `#${r}${g}${b}`;
        },
        
        rgbToHex(rgb) {
            if (!Array.isArray(rgb) || rgb.length < 3) return '#000000';
            const [r, g, b] = rgb.map(v => Math.max(0, Math.min(255, Number(v) || 0)));
            return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('');
        },

        hexToRgb(hex) {
            if (typeof hex !== 'string') return null;
            const m = hex.trim().match(/^#?([0-9a-fA-F]{6})$/);
            if (!m) return null;
            const intVal = parseInt(m[1], 16);
            return [(intVal >> 16) & 255, (intVal >> 8) & 255, intVal & 255];
        },

        async setSlotColor(slotIndex, instanceIndex, hex, toolNumber = null, material = '', temp = 0) {
            const rgb = this.hexToRgb(hex);
            if (!rgb) {
                this.showNotification('Invalid color', 'error');
                return;
            }
            const payload = {};
            if (toolNumber !== null && toolNumber !== undefined) {
                payload.T = toolNumber;
            } else {
                payload.INDEX = slotIndex;
            }
            payload.COLOR = `${rgb[0]},${rgb[1]},${rgb[2]}`; // ACE driver expects R,G,B or named color
            const safeMaterial = (typeof material === 'string' && material.trim()) ? material.trim() : 'PLA';
            let safeTemp = Number(temp);
            if (!Number.isFinite(safeTemp) || safeTemp <= 0 || safeTemp > 300) {
                safeTemp = 200;
            }
            payload.MATERIAL = safeMaterial;
            payload.TEMP = safeTemp;
            const success = await this.executeCommand('ACE_SET_SLOT', payload);
            if (success) {
                // Keep local state; periodic refresh will pull latest
            }
        },

        async setSlotMaterial(slotIndex, instanceIndex, toolNumber, material, currentColor, currentTemp) {
            const panel = this.instancesPanels.find(p => p.index === instanceIndex);
            const locked = (panel && panel.rfidSyncEnabled) ? (panel.slots.find(s => s.index === slotIndex)?.rfid === 2) : false;
            if (locked) {
                this.showNotification('RFID locked: disable RFID sync to edit', 'error');
                return;
            }
            const hex = this.getColorHex(currentColor);
            const rgb = this.hexToRgb(hex);
            const tempDefault = this.materialOptions[material] || currentTemp || 200;
            const payload = {};
            if (toolNumber !== null && toolNumber !== undefined) {
                payload.T = toolNumber;
            } else {
                payload.INDEX = slotIndex;
            }
            payload.COLOR = Array.isArray(rgb) ? `${rgb[0]},${rgb[1]},${rgb[2]}` : hex || '255,255,255';
            payload.MATERIAL = material && material.trim() ? material.trim() : 'PLA';
            payload.TEMP = Math.max(1, Math.min(300, tempDefault));
            const success = await this.executeCommand('ACE_SET_SLOT', payload);
            if (success) {
                setTimeout(() => this.loadStatus(), 500);
            }
        },

        async setSlotTemp(slotIndex, instanceIndex, toolNumber, tempValue, currentColor, currentMaterial) {
            const panel = this.instancesPanels.find(p => p.index === instanceIndex);
            const locked = (panel && panel.rfidSyncEnabled) ? (panel.slots.find(s => s.index === slotIndex)?.rfid === 2) : false;
            if (locked) {
                this.showNotification('RFID locked: disable RFID sync to edit', 'error');
                return;
            }
            const hex = this.getColorHex(currentColor);
            const rgb = this.hexToRgb(hex);
            let temp = Number(tempValue);
            if (!Number.isFinite(temp) || temp <= 0 || temp > 300) {
                temp = 200;
            }
            const payload = {};
            if (toolNumber !== null && toolNumber !== undefined) {
                payload.T = toolNumber;
            } else {
                payload.INDEX = slotIndex;
            }
            payload.COLOR = Array.isArray(rgb) ? `${rgb[0]},${rgb[1]},${rgb[2]}` : hex || '255,255,255';
            payload.MATERIAL = currentMaterial && currentMaterial.trim() ? currentMaterial.trim() : 'PLA';
            payload.TEMP = temp;
            const success = await this.executeCommand('ACE_SET_SLOT', payload);
            if (success) {
                setTimeout(() => this.loadStatus(), 500);
            }
        },

        commitSlotTemp(slotIndex, instanceIndex, toolNumber, event, currentColor, currentMaterial) {
            const val = event && event.target ? event.target.value : null;
            const parsed = Number(val);
            const temp = (!Number.isFinite(parsed) || parsed <= 0 || parsed > 300) ? 200 : parsed;
            if (event && event.target) {
                event.target.value = temp;
            }
            this.setSlotTemp(slotIndex, instanceIndex, toolNumber, temp, currentColor, currentMaterial);
            this.updateLocalSlotTemp(instanceIndex, slotIndex, temp);
        },

        updateLocalSlotTemp(instanceIndex, slotIndex, temp) {
            // Update in instancesPanels
            this.instancesPanels = this.instancesPanels.map(panel => {
                if (panel.index !== instanceIndex) return panel;
                const slots = panel.slots.map(slot => {
                    if (slot.index === slotIndex) {
                        return { ...slot, temp };
                    }
                    return slot;
                });
                return { ...panel, slots };
            });
            // Update current slots view if matching instance
            if (this.selectedInstance === instanceIndex) {
                this.slots = this.slots.map(slot => {
                    if (slot.index === slotIndex) {
                        return { ...slot, temp };
                    }
                    return slot;
                });
            }
        },

        getSlotToolNumber(slot, instanceIndex) {
            if (slot && slot.tool !== null && slot.tool !== undefined) {
                const toolNum = Number(slot.tool);
                return Number.isInteger(toolNum) ? toolNum : null;
            }

            const slotIndex = Number(slot?.index);
            if (!Number.isInteger(slotIndex)) {
                return null;
            }

            // If slot.tool is missing, derive instance tool offset from known slots.
            const panel = this.instancesPanels.find(p => p.index === instanceIndex);
            if (panel && Array.isArray(panel.slots)) {
                const sample = panel.slots.find(s => Number.isInteger(Number(s?.tool)) && Number.isInteger(Number(s?.index)));
                if (sample) {
                    const sampleTool = Number(sample.tool);
                    const sampleIndex = Number(sample.index);
                    const offset = sampleTool - sampleIndex;
                    return offset + slotIndex;
                }
            }

            // Legacy single-instance fallback.
            if (this.instancesPanels.length <= 1) {
                return slotIndex;
            }
            return null;
        },

        isCurrentToolSlot(slot, instanceIndex) {
            if (!Number.isInteger(this.currentTool) || this.currentTool < 0) {
                return false;
            }
            const slotTool = this.getSlotToolNumber(slot, instanceIndex);
            return slotTool !== null && slotTool === this.currentTool;
        },
        
        openColorPicker(instanceIndex, slotIndex, currentColor, toolNumber, material, temp, slotObj) {
            const panel = this.instancesPanels.find(p => p.index === instanceIndex);
            const locked = (panel && panel.rfidSyncEnabled && slotIndex !== undefined) ? (panel.slots.find(s => s.index === slotIndex)?.rfid === 2) : false;
            if (locked) return;
            this.colorPickerTarget = { instanceIndex, slotIndex, toolNumber, material, temp, slotObj };
            const picker = this.$refs.globalColorPicker;
            if (picker) {
                const hex = slotObj?.hex || this.getColorHex(currentColor);
                picker.value = hex;
                picker.click();
            }
        },

        handleColorPicked(event) {
            if (!this.colorPickerTarget) return;
            const { instanceIndex, slotIndex, toolNumber, material, temp, slotObj } = this.colorPickerTarget;
            const hex = event.target.value;
            if (slotObj) slotObj.hex = hex;
            this.setSlotColor(slotIndex, instanceIndex, hex, toolNumber, material, temp);
            this.colorPickerTarget = null;
        },
        
        formatTime(minutes) {
            if (!minutes || minutes <= 0) return `0 ${this.t('time.minutes')}`;
            const hours = Math.floor(minutes / 60);
            const mins = minutes % 60;
            if (hours > 0) {
                return `${hours}${this.t('time.hours')} ${mins}${this.t('time.minutesShort')}`;
            }
            return `${mins} ${this.t('time.minutes')}`;
        },
        
        formatRemainingTime(minutes) {
            // Форматирует оставшееся время сушки в формате "119м 59с"
            // minutes может быть дробным числом (119.983 = 119 минут 59 секунд)
            if (!minutes || minutes <= 0) return `0${this.t('time.minutesShort')} 0${this.t('time.secondsShort')}`;
            
            const totalMinutes = Math.floor(minutes);
            const fractionalPart = minutes - totalMinutes;
            const seconds = Math.round(fractionalPart * 60);
            
            if (totalMinutes > 0) {
                if (seconds > 0) {
                    return `${totalMinutes}${this.t('time.minutesShort')} ${seconds}${this.t('time.secondsShort')}`;
                }
                return `${totalMinutes}${this.t('time.minutesShort')}`;
            }
            return `${seconds}${this.t('time.secondsShort')}`;
        },
        
        showNotification(message, type = 'info') {
            this.notification = {
                show: true,
                message: message,
                type: type
            };
            
            setTimeout(() => {
                this.notification.show = false;
            }, 3000);
        }
    }
    }).mount('#app');
