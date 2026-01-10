// ========== VARIABILI GLOBALI ==========
const socket = io('/data');
let isAcquiring = false;
let deviceConnected = false;
let shouldUpdateCharts = false;
let shouldUpdateCounters = false;
let acquisitionStartTime = null; // Timestamp inizio acquisizione

let frozenStats = {
    ecg: 0,
    adc: 0,
    packets: 0,
    temp: '--'
};

let temperatureHistory = {
    values: [],
    times: []
};

let lastTemperature = null;
let lastTempUpdateTime = null; // Timestamp ultimo aggiornamento TEMP
const MAX_TEMP_DISPLAY = 1200;
const TEMP_UPDATE_INTERVAL = 120000; // 2 minuti in millisecondi

// ========== HELPER TIMESTAMP ==========
function formatTimestamp(milliseconds, format = 'full') {
    const date = new Date(milliseconds);
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    
    if (format === 'minutes') {
        return `${hours}:${minutes}`;
    }
    return `${hours}:${minutes}:${seconds}`;
}

function getTimeRange(numSamples, sampleRate) {
    // Calcola il range temporale degli ultimi N samples
    const now = Date.now();
    const durationMs = (numSamples / sampleRate) * 1000;
    const windowStart = now - durationMs;
    
    return {
        start: formatTimestamp(windowStart),
        end: formatTimestamp(now),
        startMs: windowStart,
        endMs: now
    };
}

// ========== FUNZIONI LAYOUT GRAFICI ==========
const getChartLayout = (title, yAxisLabel = 'Value', xAxisLabel = 'Time') => ({
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: '#0f172a',
    font: {
        color: '#f8fafc',
        family: 'Inter, sans-serif',
        size: 11
    },
    xaxis: { 
        gridcolor: '#1e293b',
        title: xAxisLabel,
        titlefont: { size: 11 },
        showline: true,
        linecolor: '#334155',
        zeroline: false
    },
    yaxis: { 
        gridcolor: '#1e293b',
        title: yAxisLabel,
        titlefont: { size: 11 },
        showline: true,
        linecolor: '#334155',
        zeroline: false
    },
    margin: { l: 60, r: 30, t: 20, b: 50 },
    hovermode: 'closest',
    showlegend: true,
    legend: {
        x: 1,
        xanchor: 'right',
        y: 1,
        bgcolor: 'rgba(30, 41, 59, 0.9)',
        bordercolor: '#334155',
        borderwidth: 1,
        font: { size: 10 }
    }
});

const chartConfig = { 
    responsive: true,
    displayModeBar: true,
    modeBarButtonsToRemove: ['lasso2d', 'select2d', 'toImage'],
    displaylogo: false
};

// ========== INIZIALIZZAZIONE GRAFICI ==========
function initializeCharts() {
    Plotly.newPlot('ecgChart', [{
        y: [],
        x: [],
        type: 'scatter',
        mode: 'lines',
        line: { color: '#10b981', width: 1.5 },
        name: 'ECG',
        hovertemplate: 'Time: %{x}<br>Value: %{y}<extra></extra>'
    }], getChartLayout('ECG Signal', 'Amplitude', 'Time'), chartConfig);

    Plotly.newPlot('adcChart', [], getChartLayout('ADC Channels', 'Value', 'Time'), chartConfig);

    Plotly.newPlot('tempChart', [{
        y: [],
        x: [],
        type: 'scatter',
        mode: 'lines+markers',
        line: { color: '#f59e0b', width: 2.5 },
        marker: { size: 7, color: '#f59e0b', line: { color: '#fff', width: 1 } },
        name: 'Temperature',
        hovertemplate: 'Time: %{x}<br>Temp: %{y:.1f}°C<extra></extra>'
    }], getChartLayout('Temperature', 'Temperature (°C)', 'Time'), chartConfig);
}

// ========== GESTIONE NAVIGAZIONE ==========

function showRealtimeView() {
    // Hide all views
    document.querySelectorAll('.realtime-view, .history-view, .anomalies-view, .expert-view, .settings-view, .simulate-anomaly-view, .debug-view, .models-view').forEach(view => {
        view.classList.remove('active');
    });
    
    // Show realtime view
    document.getElementById('realtimeView').classList.add('active');
    
    // Update sidebar active state
    updateSidebarActive('realtime');
    
    setTimeout(() => {
        Plotly.Plots.resize('ecgChart');
        Plotly.Plots.resize('adcChart');
        Plotly.Plots.resize('tempChart');
    }, 100);
}

function showHomeMenu() {
    // Deprecated - redirect to realtime view
    showRealtimeView();
}

function updateSidebarActive(viewName) {
    document.querySelectorAll('.sidebar-item').forEach(item => {
        if (!item.classList.contains('sidebar-item-expandable')) {
            item.classList.remove('active');
        }
    });
    const activeItem = document.querySelector(`.sidebar-item[data-view="${viewName}"]`);
    if (activeItem) {
        activeItem.classList.add('active');
    }
}

// ========== EXPERT MODE TOGGLE ==========

function toggleExpertMode() {
    const checkbox = document.getElementById('expertModeToggle');
    const submenu = document.getElementById('expertSubmenu');
    
    if (checkbox.checked) {
        submenu.classList.add('expanded');
    } else {
        submenu.classList.remove('expanded');
    }
}

function showAvailableModels() {
    alert('Funzionalità in sviluppo');
    return false;
}

function showSimulateAnomaly() {
    document.querySelectorAll('.realtime-view, .history-view, .anomalies-view, .expert-view, .settings-view, .simulate-anomaly-view, .debug-view, .models-view').forEach(view => {
        view.classList.remove('active');
    });
    document.getElementById('simulateAnomalyView').classList.add('active');
    updateSidebarActive('simulate');
}

// ========== SIMULATE ANOMALY FUNCTIONS ==========

let currentAnomalyType = 'ecg';

function selectAnomalyType(type) {
    currentAnomalyType = type;
    
    // Update button states
    document.querySelectorAll('.type-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    document.querySelector(`.type-btn[data-type="${type}"]`).classList.add('active');
    
    // Show/hide parameter panels
    if (type === 'temp') {
        document.getElementById('ecgPiezoParams').style.display = 'none';
        document.getElementById('tempParams').style.display = 'block';
        // Validate initial temperature
        setTimeout(validateTemperature, 10);
    } else {
        document.getElementById('ecgPiezoParams').style.display = 'block';
        document.getElementById('tempParams').style.display = 'none';
        // Enable button for ECG/PIEZO
        document.querySelector('.btn-simulate').disabled = false;
    }
    
    // Hide result
    document.getElementById('simulateResult').style.display = 'none';
}

function updateTempThreshold() {
    // This function is called when temp type changes
    // Threshold is now fixed server-side, no need to update UI
}

function validateTemperature() {
    const tempType = document.querySelector('input[name="tempType"]:checked')?.value;
    const temperatureInput = document.getElementById('temperature');
    const temperature = parseFloat(temperatureInput.value);
    const validationMessage = document.getElementById('tempValidationMessage');
    const generateBtn = document.querySelector('.btn-simulate');
    
    if (!tempType || isNaN(temperature)) {
        validationMessage.textContent = '';
        validationMessage.className = 'validation-message';
        temperatureInput.classList.remove('valid', 'invalid');
        return;
    }
    
    let isValid = false;
    let message = '';
    
    if (tempType === 'hypothermia') {
        // Hypothermia: temperatura deve essere < 35°C
        if (temperature >= 35.0) {
            isValid = false;
            message = `Errore: per ipotermia la temperatura deve essere < 35°C (attuale: ${temperature}°C)`;
        } else {
            isValid = true;
            message = `Valido: temperatura ${temperature}°C < 35°C`;
        }
    } else if (tempType === 'hyperthermia') {
        // Hyperthermia: temperatura deve essere > 37.5°C
        if (temperature <= 37.5) {
            isValid = false;
            message = `Errore: per ipertermia la temperatura deve essere > 37.5°C (attuale: ${temperature}°C)`;
        } else {
            isValid = true;
            message = `Valido: temperatura ${temperature}°C > 37.5°C`;
        }
    }
    
    // Update UI
    if (isValid) {
        temperatureInput.classList.remove('invalid');
        temperatureInput.classList.add('valid');
        validationMessage.textContent = message;
        validationMessage.className = 'validation-message success';
        generateBtn.disabled = false;
    } else {
        temperatureInput.classList.remove('valid');
        temperatureInput.classList.add('invalid');
        validationMessage.textContent = message;
        validationMessage.className = 'validation-message error';
        generateBtn.disabled = true;
    }
}

async function generateAnomaly() {
    const resultDiv = document.getElementById('simulateResult');
    const generateBtn = document.querySelector('.btn-simulate');
    
    // Disable button during request
    generateBtn.disabled = true;
    generateBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg> Generazione...';
    
    try {
        let payload = {
            type: currentAnomalyType
        };
        
        if (currentAnomalyType === 'ecg' || currentAnomalyType === 'piezo') {
            // ECG/PIEZO parameters
            const reconstructionError = document.getElementById('reconstructionError').value;
            const threshold = document.getElementById('threshold').value;
            
            if (reconstructionError) {
                payload.reconstruction_error = parseFloat(reconstructionError);
            }
            if (threshold) {
                payload.threshold = parseFloat(threshold);
            }
            
        } else if (currentAnomalyType === 'temp') {
            // TEMP parameters
            const tempType = document.querySelector('input[name="tempType"]:checked').value;
            const temperature = parseFloat(document.getElementById('temperature').value);
            const durationMinutes = parseInt(document.getElementById('durationMinutes').value);
            const severity = document.querySelector('input[name="severity"]:checked').value;
            
            if (!temperature) {
                throw new Error('Temperatura richiesta');
            }
            
            // Validate temperature against type
            if (tempType === 'hypothermia' && temperature >= 35.0) {
                throw new Error('Per ipotermia la temperatura deve essere < 35°C');
            }
            if (tempType === 'hyperthermia' && temperature <= 37.5) {
                throw new Error('Per ipertermia la temperatura deve essere > 37.5°C');
            }
            
            // Validate duration is multiple of 2
            if (durationMinutes % 2 !== 0) {
                throw new Error('La durata deve essere un multiplo di 2 (es: 2, 4, 6, 8, 10...)');
            }
            
            // Convert minutes to readings (divide by 2)
            const durationReadings = durationMinutes / 2;
            
            // Set fixed threshold based on type
            const threshold = tempType === 'hypothermia' ? 35.0 : 37.5;
            
            payload.anomaly_type = tempType;
            payload.temperature = temperature;
            payload.threshold = threshold;
            payload.duration_readings = durationReadings;
            
            if (severity !== 'auto') {
                payload.severity = severity;
            }
        }
        
        // Send request to backend
        const response = await fetch('/api/simulate/anomaly', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
        
        const data = await response.json();
        
        if (response.ok) {
            // Success
            const typeLabels = {
                'ecg': 'ECG',
                'piezo': 'PIEZO',
                'temp': 'Temperatura'
            };
            const typeName = typeLabels[currentAnomalyType] || currentAnomalyType.toUpperCase();
            
            resultDiv.className = 'simulate-result success';
            resultDiv.innerHTML = `
                Anomalia ${typeName} generata con successo<br>
                <small>Timestamp: ${data.anomaly.timestamp}</small>
            `;
            resultDiv.style.display = 'block';
            
            // Reset form after 3 seconds
            setTimeout(() => {
                resultDiv.style.display = 'none';
            }, 5000);
            
        } else {
            throw new Error(data.error || 'Errore sconosciuto');
        }
        
    } catch (error) {
        console.error('Error generating anomaly:', error);
        resultDiv.className = 'simulate-result error';
        resultDiv.innerHTML = ` Errore: ${error.message}`;
        resultDiv.style.display = 'block';
    } finally {
        // Re-enable button
        generateBtn.disabled = false;
        generateBtn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="23 4 23 10 17 10"/>
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
            </svg>
            Genera Anomalia
        `;
    }
}

// ========== MODAL RESET ==========

function showExpertMode() {
    toggleExpertMode();
}

function showSettings() {
    console.log('[DEBUG] showSettings() called');
    document.querySelectorAll('.realtime-view, .history-view, .anomalies-view, .expert-view, .settings-view, .simulate-anomaly-view, .debug-view, .models-view').forEach(view => {
        view.classList.remove('active');
    });
    document.getElementById('settingsView').classList.add('active');
    updateSidebarActive('settings');
    
    // Attach event handlers to buttons
    setTimeout(() => {
        const refreshBtn = document.getElementById('refreshPortsBtn');
        const saveBtn = document.getElementById('saveConfigBtn');
        
        if (refreshBtn) {
            refreshBtn.onclick = refreshSerialPorts;
            console.log('[DEBUG] refreshBtn.onclick attached');
        }
        
        if (saveBtn) {
            saveBtn.onclick = saveSerialPortsConfig;
            console.log('[DEBUG] saveBtn.onclick attached');
        }
        
        // Auto-load USB ports
        if (typeof refreshSerialPorts === 'function') {
            console.log('[DEBUG] refreshSerialPorts exists, calling it');
            refreshSerialPorts();
        } else {
            console.error('[ERROR] refreshSerialPorts is not defined!');
        }
    }, 100);
}

// ========== AGGIORNAMENTO GRAFICI ==========
function updateChart(signal, data) {
    const chartId = signal.toLowerCase() + 'Chart';
    
    if (!data || !data.y || data.y.length === 0) {
        return;
    }
    
    if (signal === 'ECG') {
        const yData = data.y[0] || [];
        const numSamples = yData.length;
        
        // Usa indici numerici per mantenere la forma del grafico
        const xData = Array.from({length: numSamples}, (_, i) => i);
        
        // Calcola range temporale per label
        const timeRange = getTimeRange(numSamples, 250);
        
        Plotly.update(chartId, {
            y: [yData],
            x: [xData]
        }, {
            xaxis: {
                gridcolor: '#1e293b',
                title: `Time (${timeRange.start} - ${timeRange.end})`,
                showline: true,
                linecolor: '#334155',
                zeroline: false
            }
        }, [0]);
        
        // Mostra range temporale
        document.getElementById('ecgDataPoints').textContent = 
            `${timeRange.start} - ${timeRange.end}`;
        
    } else if (signal === 'ADC') {
        const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ec4899'];
        const numSamples = data.y[0] ? data.y[0].length : 0;
        
        // Usa indici numerici per mantenere la forma del grafico
        const xData = Array.from({length: numSamples}, (_, i) => i);
        
        // Calcola range temporale per label
        const timeRange = getTimeRange(numSamples, 250);
        
        const traces = data.y.map((channelData, i) => ({
            y: channelData,
            x: xData,
            type: 'scatter',
            mode: 'lines',
            name: `Channel ${i + 1}`,
            line: { 
                color: colors[i % colors.length], 
                width: 1.5 
            },
            hovertemplate: `CH${i+1}: %{y}<extra></extra>`
        }));
        
        const layout = getChartLayout('ADC Channels', 'Value', `Time (${timeRange.start} - ${timeRange.end})`);
        Plotly.react(chartId, traces, layout, chartConfig);
        
        // Mostra range temporale
        document.getElementById('adcDataPoints').textContent = 
            `${timeRange.start} - ${timeRange.end}`;
        
    } else if (signal === 'TEMP') {
        const tempData = data.y[0] || [];
        
        if (tempData.length > 0) {
            let rawValue = tempData[0];
            if (Array.isArray(rawValue)) {
                rawValue = rawValue[0];
            }
            
            const tempInCelsius = rawValue / 100;
            
            temperatureHistory.values.push(tempInCelsius);
            
            // Calcola MINUTI dall'inizio (1 sample ogni 2 MINUTI)
            const minutesFromStart = (temperatureHistory.values.length - 1) * 2;
            temperatureHistory.times.push(minutesFromStart);
            
            // Calcola timestamp per display
            const sampleIndex = temperatureHistory.values.length - 1;
            const sampleTimeMs = acquisitionStartTime ? 
                new Date(acquisitionStartTime).getTime() + (sampleIndex * 120000) : 
                Date.now();
            const timeString = formatTimestamp(sampleTimeMs, 'minutes');
            
            document.getElementById('tempValue').textContent = tempInCelsius.toFixed(1);
            
            // Preparazione dati per grafico
            const displayValues = temperatureHistory.values.slice(-MAX_TEMP_DISPLAY);
            const displayTimes = temperatureHistory.times.slice(-MAX_TEMP_DISPLAY);
            
            // USA MINUTI EFFETTIVI sull'asse X 
            const xMinutes = displayTimes; 
            
            // Calcola timestamp labels per display
            const firstSampleMs = acquisitionStartTime ? 
                new Date(acquisitionStartTime).getTime() + (displayTimes[0] * 60000) : 
                Date.now() - (displayTimes[displayTimes.length - 1] - displayTimes[0]) * 60000;
            const lastSampleMs = firstSampleMs + ((displayTimes[displayTimes.length - 1] - displayTimes[0]) * 60000);
            
            const startLabel = formatTimestamp(firstSampleMs, 'minutes');
            const endLabel = formatTimestamp(lastSampleMs, 'minutes');
            
            // Mostra range temporale
            if (displayTimes.length > 0) {
                document.getElementById('tempDataPoints').textContent = 
                    `${startLabel} - ${endLabel}`;
            }
            
            const layout = getChartLayout('Temperature', 'Temperature (°C)', 
                displayTimes.length > 0 ? `Time (${startLabel} - ${endLabel})` : 'Time');
            
            Plotly.react(chartId, [{
                y: [...displayValues],
                x: xMinutes,  // USA MINUTI REALI: 0, 2, 4, 6, 8...
                type: 'scatter',
                mode: 'lines+markers',
                line: { color: '#f59e0b', width: 2.5 },
                marker: { 
                    size: 7, 
                    color: '#f59e0b', 
                    line: { color: '#fff', width: 1 } 
                },
                name: 'Temperature',
                hovertemplate: 'Temp: %{y:.1f}°C<extra></extra>'
            }], layout, chartConfig);
        }
    }
}

// ========== AGGIORNAMENTO STATISTICHE ==========
function updateStatistics(data) {
    if (!shouldUpdateCounters) {
        console.log('Counters frozen - skipping update');
        return;
    }
    
    if (data.stats.TEMP.current_temp !== null) {
        const tempC = data.stats.TEMP.current_temp / 100;
        document.getElementById('tempValue').textContent = tempC.toFixed(1);
        frozenStats.temp = tempC.toFixed(1);
        
        if (lastTemperature === null || Math.abs(tempC - lastTemperature) > 0.01) {
            lastTemperature = tempC;
            
            temperatureHistory.values.push(tempC);
            
            // Calcola MINUTI dall'inizio (1 sample ogni 2 MINUTI)
            const minutesFromStart = (temperatureHistory.values.length - 1) * 2;
            temperatureHistory.times.push(minutesFromStart);
            
            const displayValues = temperatureHistory.values.slice(-MAX_TEMP_DISPLAY);
            const displayTimes = temperatureHistory.times.slice(-MAX_TEMP_DISPLAY);
            
            // USA MINUTI EFFETTIVI sull'asse X 
            const xMinutes = displayTimes;
            
            // Calcola timestamp labels per display
            const sampleIndex = temperatureHistory.values.length - 1;
            const firstSampleMs = acquisitionStartTime ? 
                new Date(acquisitionStartTime).getTime() + (displayTimes[0] * 60000) : 
                Date.now() - (displayTimes[displayTimes.length - 1] - displayTimes[0]) * 60000;
            const lastSampleMs = firstSampleMs + ((displayTimes[displayTimes.length - 1] - displayTimes[0]) * 60000);
            
            const startLabel = formatTimestamp(firstSampleMs, 'minutes');
            const endLabel = formatTimestamp(lastSampleMs, 'minutes');
            
            // Mostra range temporale
            if (displayTimes.length > 0) {
                document.getElementById('tempDataPoints').textContent = 
                    `${startLabel} - ${endLabel}`;
            }
            
            const layout = getChartLayout('Temperature', 'Temperature (°C)', 
                displayTimes.length > 0 ? `Time (${startLabel} - ${endLabel})` : 'Time');
            
            Plotly.react('tempChart', [{
                y: [...displayValues],
                x: xMinutes,  // USA MINUTI REALI: 0, 2, 4, 6, 8...
                type: 'scatter',
                mode: 'lines+markers',
                line: { color: '#f59e0b', width: 2.5 },
                marker: { 
                    size: 7, 
                    color: '#f59e0b', 
                    line: { color: '#fff', width: 1 } 
                },
                name: 'Temperature',
                hovertemplate: 'Temp: %{y:.1f}°C<extra></extra>'
            }], layout, chartConfig);
        }
    }
    
    frozenStats.ecg = data.stats.ECG.samples;
    frozenStats.adc = data.stats.ADC.samples;
    frozenStats.packets = data.packet_count;
}

// ========== AGGIORNAMENTO STATUS ==========

let connectionErrorShown = false;
let connectionErrorCooldown = false; // Track if user manually closed it

function updateDeviceStatus(connected) {
    const badge = document.getElementById('deviceStatus');
    
    if (connected) {
        badge.className = 'status-indicator status-connected';
        badge.innerHTML = '<span class="status-dot"></span><span>Connected</span>';
        
        // Remove connection error notification if exists and reset cooldown
        if (connectionErrorShown) {
            removeConnectionErrorNotification();
            connectionErrorShown = false;
        }
        connectionErrorCooldown = false; // Reset cooldown when reconnected
    } else {
        badge.className = 'status-indicator status-disconnected';
        badge.innerHTML = '<span class="status-dot"></span><span>Disconnected</span>';
        
        // Show connection error notification only if not shown and not in cooldown
        if (!connectionErrorShown && !connectionErrorCooldown) {
            showConnectionErrorNotification();
            connectionErrorShown = true;
        }
    }
}

function showConnectionErrorNotification() {
    // Check if notification already exists
    if (document.getElementById('connectionErrorNotification')) {
        return;
    }
    
    const notification = document.createElement('div');
    notification.id = 'connectionErrorNotification';
    notification.className = 'notification connection-error persistent';
    
    notification.innerHTML = `
        <div class="notification-icon" style="background: rgba(239, 68, 68, 0.15); color: #ef4444;">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="12"/>
                <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
        </div>
        <div class="notification-content">
            <div class="notification-header">
                <div class="notification-title">
                    ⚠️ Errore di Connessione
                </div>
                <button class="notification-close" onclick="closeConnectionError()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="18" y1="6" x2="6" y2="18"/>
                        <line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>
            <div class="notification-message" style="margin-bottom: 0.75rem;">
                Non è stato possibile connettersi al dispositivo.
            </div>
            <div class="connection-error-steps">
                <div class="error-step">
                    <div class="step-number">1</div>
                    <div class="step-text">Controllare che la T-Shirt sia accesa</div>
                </div>
                <div class="error-step">
                    <div class="step-number">2</div>
                    <div class="step-text">Verificare che Data Port e Shell Port siano correttamente configurate nelle Impostazioni</div>
                </div>
                <div class="error-step">
                    <div class="step-number">3</div>
                    <div class="step-text">Assicurarsi che il dongle USB sia correttamente collegato</div>
                </div>
            </div>
        </div>
    `;
    
    const container = document.getElementById('notificationsContainer');
    if (!container) {
        const newContainer = document.createElement('div');
        newContainer.id = 'notificationsContainer';
        newContainer.className = 'notifications-container';
        document.body.appendChild(newContainer);
        newContainer.appendChild(notification);
    } else {
        container.appendChild(notification);
    }
}

function removeConnectionErrorNotification() {
    const notification = document.getElementById('connectionErrorNotification');
    if (notification) {
        notification.classList.add('closing');
        setTimeout(() => {
            if (notification.parentElement) {
                notification.parentElement.removeChild(notification);
            }
        }, 300);
    }
}

function closeConnectionError() {
    connectionErrorShown = false;
    connectionErrorCooldown = true; // Activate cooldown
    removeConnectionErrorNotification();
    
    // Clear cooldown after 2 minutes (120000 ms)
    setTimeout(() => {
        connectionErrorCooldown = false;
        // If still disconnected after cooldown, show notification again
        const badge = document.getElementById('deviceStatus');
        if (badge && badge.textContent.includes('Disconnected') && !connectionErrorShown) {
            showConnectionErrorNotification();
            connectionErrorShown = true;
        }
    }, 120000); // 2 minutes
}

function updateAcquisitionStatus(acquiring) {
    const badge = document.getElementById('acquisitionStatus');
    const btnStart = document.getElementById('btnStart');
    const btnStop = document.getElementById('btnStop');
    const btnReset = document.getElementById('btnReset');
    
    if (acquiring) {
        badge.className = 'status-indicator status-acquiring';
        badge.innerHTML = '<span class="status-dot"></span><span>Acquiring</span>';
        btnStart.disabled = true;
        btnStop.disabled = false;
        btnReset.disabled = true;
    } else {
        badge.className = 'status-indicator status-idle';
        badge.innerHTML = '<span class="status-dot"></span><span>Idle</span>';
        btnStart.disabled = false;
        btnStop.disabled = true;
        btnReset.disabled = false;
    }
}

// ========== MODAL RESET ==========
function showResetModal() {
    document.getElementById('resetModal').classList.add('show', 'active');
}

function closeResetModal() {
    document.getElementById('resetModal').classList.remove('show', 'active');
}

function confirmReset() {
    closeResetModal();
    performReset();
}

function performReset() {
    console.log('Resetting dashboard...');
    
    // Reset timestamp acquisizione
    acquisitionStartTime = null;
    
    // Reset temperature history
    temperatureHistory = {
        values: [],
        times: []
    };
    lastTemperature = null;
    
    // Reset frozen stats
    frozenStats = {
        ecg: 0,
        adc: 0,
        packets: 0,
        temp: '--'
    };
    
    // Reset UI elements
    document.getElementById('tempValue').textContent = '--';
    document.getElementById('ecgDataPoints').textContent = '--';
    document.getElementById('adcDataPoints').textContent = '--';
    document.getElementById('tempDataPoints').textContent = '--';
    
    // Force empty charts
    Plotly.react('ecgChart', [{
        y: [],
        x: [],
        type: 'scatter',
        mode: 'lines',
        line: { color: '#10b981', width: 1.5 },
        name: 'ECG',
        hovertemplate: 'Time: %{x}<br>Value: %{y}<extra></extra>'
    }], getChartLayout('ECG Signal', 'Amplitude', 'Time'), chartConfig);
    
    Plotly.react('adcChart', [], getChartLayout('ADC Channels', 'Value', 'Time'), chartConfig);
    
    Plotly.react('tempChart', [{
        y: [],
        x: [],
        type: 'scatter',
        mode: 'lines+markers',
        line: { color: '#f59e0b', width: 2.5 },
        marker: { size: 7, color: '#f59e0b', line: { color: '#fff', width: 1 } },
        name: 'Temperature',
        hovertemplate: 'Time: %{x}<br>Temp: %{y:.1f}°C<extra></extra>'
    }], getChartLayout('Temperature', 'Temperature (°C)', 'Time'), chartConfig);
    
    console.log('Dashboard reset complete');
}

// ========== EVENTI SOCKETIO ==========
socket.on('connect', () => {
    console.log('Connected to server');
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
});

socket.on('data_update', (data) => {
    if (data.signal === 'TEMP') {
        updateChart(data.signal, data.data);
    } else {
        if (shouldUpdateCharts) {
            updateChart(data.signal, data.data);
        }
    }
});

socket.on('status_update', (data) => {
    updateStatistics(data);
});

socket.on('device_status', (data) => {
    deviceConnected = data.connected;
    updateDeviceStatus(data.connected);
});

socket.on('acquisition_status', (data) => {
    isAcquiring = data.acquiring;
    shouldUpdateCharts = data.acquiring;
    shouldUpdateCounters = data.acquiring;
    
    // Salva timestamp inizio acquisizione
    if (data.acquiring && data.start_time) {
        acquisitionStartTime = data.start_time;
        console.log('[Timestamp] Acquisition started at:', acquisitionStartTime);
    }
    
    updateAcquisitionStatus(data.acquiring);
});

// ========== GESTIONE PULSANTI ==========
document.addEventListener('DOMContentLoaded', function() {
    // Inizializza grafici
    initializeCharts();
    
    // Start button
    document.getElementById('btnStart').addEventListener('click', () => {
        fetch('/api/control/start', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                console.log('Started:', data);
                shouldUpdateCharts = true;
                shouldUpdateCounters = true;
            })
            .catch(err => console.error('Start error:', err));
    });

    // Stop button
    document.getElementById('btnStop').addEventListener('click', () => {
        console.log('Stopping acquisition...');
        fetch('/api/control/stop', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                console.log('Stopped:', data);
                shouldUpdateCharts = false;
                shouldUpdateCounters = false;
                console.log('Counters frozen at:', frozenStats);
            })
            .catch(err => console.error('Stop error:', err));
    });

    // Reset button
    document.getElementById('btnReset').addEventListener('click', showResetModal);

    // Richieste dati iniziali
    socket.emit('request_data', { signal: 'ECG' });
    socket.emit('request_data', { signal: 'ADC' });
    socket.emit('request_data', { signal: 'TEMP' });

    // Polling status periodico
    setInterval(() => {
        fetch('/api/status')
            .then(r => r.json())
            .then(data => {
                updateDeviceStatus(data.device_connected);
                updateAcquisitionStatus(data.is_acquiring);
                updateStatistics(data);
                shouldUpdateCharts = data.is_acquiring;
                shouldUpdateCounters = data.is_acquiring;
            })
            .catch(err => console.error('Status update error:', err));
    }, 5000);
});
// ========== SETTINGS - USB PORTS CONFIGURATION ==========

async function refreshSerialPorts() {
    const shellSelect = document.getElementById('shellPortSelect');
    const dataSelect = document.getElementById('dataPortSelect');
    const refreshBtn = document.querySelector('.btn-refresh');
    
    // Disable button during refresh
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg> Rilevamento...';
    
    try {
        const response = await fetch('/api/serial-ports/detect');
        const data = await response.json();
        
        if (response.ok && data.success) {
            const ports = data.ports;
            const currentConfig = data.current_config;
            
            // Clear existing options
            shellSelect.innerHTML = '';
            dataSelect.innerHTML = '';
            
            if (ports.length === 0) {
                shellSelect.innerHTML = '<option value="">Nessuna porta rilevata</option>';
                dataSelect.innerHTML = '<option value="">Nessuna porta rilevata</option>';
            } else {
                // Populate dropdowns
                ports.forEach(port => {
                    const option1 = document.createElement('option');
                    option1.value = port.device;
                    option1.textContent = `${port.device} - ${port.description}`;
                    shellSelect.appendChild(option1);
                    
                    const option2 = document.createElement('option');
                    option2.value = port.device;
                    option2.textContent = `${port.device} - ${port.description}`;
                    dataSelect.appendChild(option2);
                });
                
                // Select current config if available
                if (currentConfig) {
                    shellSelect.value = currentConfig.shell_port;
                    dataSelect.value = currentConfig.data_port;
                    
                    // Update current config display
                    document.getElementById('currentShellPort').textContent = currentConfig.shell_port;
                    document.getElementById('currentDataPort').textContent = currentConfig.data_port;
                }
            }
            
            console.log(`[Settings] Found ${ports.length} USB ports`);
        } else {
            throw new Error(data.error || 'Failed to detect ports');
        }
    } catch (error) {
        console.error('[Settings] Error detecting ports:', error);
        shellSelect.innerHTML = '<option value="">Errore rilevamento</option>';
        dataSelect.innerHTML = '<option value="">Errore rilevamento</option>';
        
        showSettingsResult('error', `Errore: ${error.message}`);
    } finally {
        // Re-enable button
        refreshBtn.disabled = false;
        refreshBtn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="23 4 23 10 17 10"/>
                <polyline points="1 20 1 14 7 14"/>
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
            </svg>
            Rileva Porte USB
        `;
    }
}

async function saveSerialPortsConfig() {
    const shellPort = document.getElementById('shellPortSelect').value;
    const dataPort = document.getElementById('dataPortSelect').value;
    const saveBtn = document.querySelector('.btn-save-config');
    
    if (!shellPort || !dataPort) {
        showSettingsResult('error', 'Seleziona entrambe le porte prima di salvare');
        return;
    }
    
    if (shellPort === dataPort) {
        showSettingsResult('error', 'Shell Port e Data Port devono essere diverse');
        return;
    }
    
    // Disable button during save
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg> Salvataggio...';
    
    try {
        const response = await fetch('/api/serial-ports/config', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                shell_port: shellPort,
                data_port: dataPort
            })
        });
        
        const data = await response.json();
        
        if (response.ok && data.success) {
            showSettingsResult('success', data.message);
            
            // Update current config display
            document.getElementById('currentShellPort').textContent = shellPort;
            document.getElementById('currentDataPort').textContent = dataPort;
            
            console.log('[Settings] Configuration saved successfully');
        } else {
            throw new Error(data.error || 'Failed to save configuration');
        }
    } catch (error) {
        console.error('[Settings] Error saving config:', error);
        showSettingsResult('error', `Errore: ${error.message}`);
    } finally {
        // Re-enable button
        saveBtn.disabled = false;
        saveBtn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
                <polyline points="17 21 17 13 7 13 7 21"/>
                <polyline points="7 3 7 8 15 8"/>
            </svg>
            Salva Configurazione
        `;
    }
}

function showSettingsResult(type, message) {
    const resultDiv = document.getElementById('settingsResult');
    resultDiv.className = `settings-result ${type}`;
    resultDiv.textContent = message;
    resultDiv.style.display = 'block';
    
    // Auto-hide after 5 seconds
    setTimeout(() => {
        resultDiv.style.display = 'none';
    }, 5000);
}