// ValgACE Dashboard Configuration
// Настройка адреса Moonraker API и WebSocket

const ACE_DASHBOARD_CONFIG = {
    // Адрес Moonraker API
    // По умолчанию используется текущий хост, но можно указать явно
    // Примеры:
    // apiBase: 'http://localhost:7125',
    // apiBase: 'http://192.168.1.100:7125',
    // apiBase: 'https://moonraker.example.com',
    apiBase: window.location.origin,
    
    // WebSocket адрес
    // По умолчанию определяется автоматически на основе apiBase
    // Примеры:
    // wsBase: 'ws://localhost:7125',
    // wsBase: 'wss://moonraker.example.com',
    wsBase: null, // null = автоматическое определение
    
    // Интервал автоматического обновления статуса (в миллисекундах)
    // По умолчанию: 5000 (5 секунд)
    autoRefreshInterval: 5000,
    
    // Таймаут для WebSocket переподключения (в миллисекундах)
    // По умолчанию: 3000 (3 секунды)
    wsReconnectTimeout: 3000,
    
    // Включить отладочные сообщения в консоль
    // Установите true для отладки проблем с загрузкой статуса
    debug: false,
    
    // Настройки по умолчанию для команд
    defaults: {
        feedLength: 50,      // Длина подачи по умолчанию (мм)
        feedSpeed: 25,        // Скорость подачи по умолчанию (мм/с)
        retractLength: 50,   // Длина отката по умолчанию (мм)
        retractSpeed: 25,    // Скорость отката по умолчанию (мм/с)
        dryingTemp: 50,      // Температура сушки по умолчанию (°C)
        dryingDuration: 240  // Длительность сушки по умолчанию (мин)
    }
};

// Функция для получения WebSocket адреса
function getWebSocketUrl() {
    if (ACE_DASHBOARD_CONFIG.wsBase) {
        return ACE_DASHBOARD_CONFIG.wsBase;
    }
    
    // Автоматическое определение на основе apiBase
    const apiBase = ACE_DASHBOARD_CONFIG.apiBase;
    if (apiBase.startsWith('https://')) {
        return apiBase.replace('https://', 'wss://') + '/websocket';
    } else if (apiBase.startsWith('http://')) {
        return apiBase.replace('http://', 'ws://') + '/websocket';
    } else {
        // Fallback на текущий хост
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${protocol}//${window.location.host}/websocket`;
    }
}

// Экспорт конфигурации (для использования в других файлах)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ACE_DASHBOARD_CONFIG, getWebSocketUrl };
}
