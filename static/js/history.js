// History JavaScript - Gestione Storico Dati
// ========== VARIABILI GLOBALI STORICO ==========
let availableSessions = [];
let selectedSession = null;
let currentWindowSize = 1000;
let currentPosition = 0;
let totalDataCount = 0;
let maxSliderPosition = 0;

// ========== HELPER FUNCTIONS FOR TIMESTAMP ==========
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

function calculateTimestampsForSamples(startSampleIndex, numSamples, sampleRate, sessionStartTime) {
    const timestamps = [];
    const sessionStartMs = new Date(sessionStartTime).getTime();
    
    for (let i = 0; i < numSamples; i++) {
        const sampleIndex = startSampleIndex + i;
        const sampleTimeMs = sessionStartMs + (sampleIndex / sampleRate) * 1000;
        timestamps.push(formatTimestamp(sampleTimeMs));
    }
    
    return timestamps;
}

// ========== NAVIGAZIONE VISTA STORICO ==========
function showHistoryView() {
    // Hide all views
    document.querySelectorAll('.realtime-view, .history-view, .anomalies-view, .expert-view, .settings-view, .simulate-anomaly-view, .debug-view, .models-view').forEach(view => {
        view.classList.remove('active');
    });
    
    // Show history view
    document.getElementById('historyView').classList.add('active');
    
    // Update sidebar active state
    updateSidebarActive('history');
    
    loadAvailableDates();
}

function updateSidebarActive(viewName) {
    document.querySelectorAll('.sidebar-item').forEach(item => {
        item.classList.remove('active');
    });
    const activeItem = document.querySelector(`.sidebar-item[data-view="${viewName}"]`);
    if (activeItem) {
        activeItem.classList.add('active');
    }
}

// ========== CARICAMENTO DATE DISPONIBILI ==========
async function loadAvailableDates() {
    const dateSelect = document.getElementById('dateSelect');
    dateSelect.innerHTML = '<option value="">Caricamento...</option>';
    
    try {
        const response = await fetch('/api/history/dates');
        const data = await response.json();
        
        if (data.dates && data.dates.length > 0) {
            dateSelect.innerHTML = '<option value="">Seleziona una data</option>';
            data.dates.forEach(date => {
                const option = document.createElement('option');
                option.value = date.value;
                option.textContent = date.label;
                dateSelect.appendChild(option);
            });
        } else {
            dateSelect.innerHTML = '<option value="">Nessuna data disponibile</option>';
        }
    } catch (error) {
        console.error('Error loading dates:', error);
        dateSelect.innerHTML = '<option value="">Errore nel caricamento</option>';
    }
    
    dateSelect.onchange = function() {
        if (this.value) {
            loadSessionsForDate(this.value);
        } else {
            document.getElementById('sessionsList').style.display = 'none';
            hideHistoryData();
        }
    };
}

// ========== CARICAMENTO SESSIONI PER DATA ==========
async function loadSessionsForDate(dateStr) {
    const sessionsList = document.getElementById('sessionsList');
    sessionsList.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">Caricamento sessioni...</p>';
    sessionsList.style.display = 'block';
    
    try {
        const response = await fetch(`/api/history/sessions/${dateStr}`);
        const data = await response.json();
        
        if (data.sessions && data.sessions.length > 0) {
            availableSessions = data.sessions;
            renderSessionsList(data.sessions);
        } else {
            sessionsList.innerHTML = `
                <div class="empty-state">
                    <p>Nessuna sessione trovata per questa data</p>
                </div>
            `;
        }
    } catch (error) {
        console.error('Error loading sessions:', error);
        sessionsList.innerHTML = `
            <div class="empty-state">
                <p style="color: var(--danger);">Errore nel caricamento delle sessioni</p>
            </div>
        `;
    }
}

// ========== RENDERING LISTA SESSIONI ==========
function renderSessionsList(sessions) {
    const sessionsList = document.getElementById('sessionsList');
    sessionsList.innerHTML = '';
    
    sessions.forEach(session => {
        const sessionCard = document.createElement('div');
        sessionCard.className = 'session-card';
        sessionCard.onclick = () => selectSession(session, sessionCard);
        
        const startTime = new Date(session.start_time);
        const endTime = session.end_time ? new Date(session.end_time) : null;
        const duration = endTime ? Math.round((endTime - startTime) / 60000) : 'In corso';
        
        sessionCard.innerHTML = `
            <div class="session-info">
                <div class="session-time">
                    ${startTime.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </div>
                <div class="session-details">
                    Durata: ${typeof duration === 'number' ? duration + ' min' : duration}
                    ECG: ${session.total_samples.ECG.toLocaleString()} 
                    ADC: ${session.total_samples.ADC.toLocaleString()} 
                    TEMP: ${session.total_samples.TEMP.toLocaleString()}
                </div>
            </div>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9 18 15 12 9 6"/>
            </svg>
        `;
        
        sessionsList.appendChild(sessionCard);
    });
}

// ========== SELEZIONE SESSIONE ==========
function selectSession(session, cardElement) {
    selectedSession = session;
    
    document.querySelectorAll('.session-card').forEach(card => {
        card.classList.remove('selected');
    });
    cardElement.classList.add('selected');
    
    document.getElementById('btnLoadHistory').disabled = false;
}

// ========== CARICAMENTO DATI STORICI ==========
async function loadHistoricalData() {
    if (!selectedSession) {
        showMessageModal('⚠️ Seleziona prima una sessione dalla lista', true);
        return;
    }
    
    const signal = document.getElementById('signalSelect').value;
    const btnLoad = document.getElementById('btnLoadHistory');
    
    btnLoad.disabled = true;
    btnLoad.innerHTML = '<span class="loading-spinner"></span> Caricamento...';
    
    try {
        const response = await fetch(
            `/api/history/window/${selectedSession.session_id}/${signal}?position=0&window_size=${currentWindowSize}`
        );
        const data = await response.json();
        
        if (data.total_count > 0) {
            displayHistoricalData(data, signal);
        } else {
            showMessageModal('Nessun dato trovato per questo segnale', false);
            hideHistoryData();
        }
    } catch (error) {
        console.error('Error loading historical data:', error);
        showMessageModal('Errore nel caricamento dei dati: ' + error.message, false);
        hideHistoryData();
    } finally {
        btnLoad.disabled = false;
        btnLoad.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="7 10 12 15 17 10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            Carica Dati
        `;
    }
}

// ========== VISUALIZZAZIONE DATI STORICI ==========
function displayHistoricalData(data, signal) {
    // Salva info totali
    totalDataCount = data.total_count;
    maxSliderPosition = data.max_position;
    currentPosition = data.window_start;
    
    document.getElementById('emptyHistoryState').style.display = 'none';
    
    const statsGrid = document.getElementById('historyStatsGrid');
    statsGrid.style.display = 'grid';
    
    document.getElementById('histSessionId').textContent = selectedSession.session_id;
    document.getElementById('histDataPoints').textContent = totalDataCount.toLocaleString();
    
    const startTime = new Date(selectedSession.start_time);
    const endTime = selectedSession.end_time ? new Date(selectedSession.end_time) : null;
    
    document.getElementById('histStartTime').textContent = 
        startTime.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
    document.getElementById('histEndTime').textContent = 
        endTime ? endTime.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' }) : 'In corso';
    
    document.getElementById('historyChartsContainer').style.display = 'block';
    
    const signalNames = {
        'ECG': 'ECG Signal',
        'ADC': 'ADC Channels',
        'TEMP': 'Temperature'
    };
    document.getElementById('historyChartTitle').textContent = signalNames[signal];
    
    // Configura slider
    const slider = document.getElementById('dataSlider');
    slider.max = maxSliderPosition;
    slider.value = currentPosition;
    
    // Renderizza grafico
    renderHistoricalChart(data.data, signal, data.window_start, data.window_end);
}

// ========== CAMBIO DIMENSIONE FINESTRA  ==========
async function changeWindowSize(newSize) {
    document.querySelectorAll('.btn-control-small').forEach(btn => {
        btn.classList.remove('active');
    });
    event.target.classList.add('active');
    
    if (newSize === -1) {
        currentWindowSize = totalDataCount;
        currentPosition = 0;
    } else {
        currentWindowSize = newSize;
        if (currentPosition + currentWindowSize > totalDataCount) {
            currentPosition = Math.max(0, totalDataCount - currentWindowSize);
        }
    }
    
    // Ricarica dati con nuova dimensione finestra
    await reloadWindowedData();
}

// ========== NAVIGAZIONE DATI (OTTIMIZZATO) ==========
async function navigateData(direction) {
    const step = Math.floor(currentWindowSize / 2);
    
    switch(direction) {
        case 'start':
            currentPosition = 0;
            break;
        case 'prev':
            currentPosition = Math.max(0, currentPosition - step);
            break;
        case 'next':
            currentPosition = Math.min(maxSliderPosition, currentPosition + step);
            break;
        case 'end':
            currentPosition = maxSliderPosition;
            break;
    }
    
    document.getElementById('dataSlider').value = currentPosition;
    
    // Ricarica dati dalla nuova posizione
    await reloadWindowedData();
}

// ========== NAVIGAZIONE CON SLIDER ==========
async function sliderNavigation(value) {
    currentPosition = parseInt(value);
    await reloadWindowedData();
}

// ========== RICARICA FINESTRA DATI ==========
async function reloadWindowedData() {
    const signal = document.getElementById('signalSelect').value;
    
    try {
        // Richiedi solo la finestra corrente al backend
        const response = await fetch(
            `/api/history/window/${selectedSession.session_id}/${signal}?position=${currentPosition}&window_size=${currentWindowSize}`
        );
        const data = await response.json();
        
        if (data.total_count > 0) {
            // Aggiorna slider
            const slider = document.getElementById('dataSlider');
            slider.max = data.max_position;
            slider.value = currentPosition;
            
            // Renderizza nuovo grafico
            renderHistoricalChart(data.data, signal, data.window_start, data.window_end);
        }
    } catch (error) {
        console.error('Error reloading windowed data:', error);
    }
}

// ========== RENDERING GRAFICO STORICO ==========
function renderHistoricalChart(chartData, signal, windowStart, windowEnd) {
    const colors = ['#10b981', '#3b82f6', '#f59e0b', '#ec4899'];
    
    // Aggiorna indicatore navigazione
    document.getElementById('navIndicator').textContent = 
        `${windowStart.toLocaleString()} - ${windowEnd.toLocaleString()} di ${totalDataCount.toLocaleString()}`;
    
    // Determina sample rate
    let sampleRate;
    if (signal === 'ECG' || signal === 'ADC') {
        sampleRate = 250; // Hz
    } else if (signal === 'TEMP') {
        sampleRate = 1; // Hz (1 sample/secondo)
    }
    
    // Calcola timestamp per display
    const numSamples = chartData.x ? chartData.x.length : (chartData.y[0] ? chartData.y[0].length : 0);
    const sessionStartMs = new Date(selectedSession.start_time).getTime();
    
    let firstSampleMs, lastSampleMs;
    
    if (signal === 'TEMP') {
        // TEMP: usa durata reale sessione (da ECG/ADC), non i sample TEMP
        const sessionDurationMs = new Date(selectedSession.end_time).getTime() - sessionStartMs;
        
        // Calcola posizione proporzionale nella sessione
        const progressRatio = currentPosition / totalDataCount;
        const windowRatio = numSamples / totalDataCount;
        
        firstSampleMs = sessionStartMs + (sessionDurationMs * progressRatio);
        lastSampleMs = sessionStartMs + (sessionDurationMs * (progressRatio + windowRatio));
    } else {
        // ECG/ADC: usa sample rate normale
        firstSampleMs = sessionStartMs + (currentPosition / sampleRate) * 1000;
        lastSampleMs = sessionStartMs + ((currentPosition + numSamples - 1) / sampleRate) * 1000;
    }
    
    const startLabel = formatTimestamp(firstSampleMs);
    const endLabel = formatTimestamp(lastSampleMs);
    
    // USA INDICI NUMERICI per l'asse X (mantiene la forma del grafico)
    const xIndices = Array.from({length: numSamples}, (_, i) => i);
    
    const timeRange = numSamples > 0 ? 
        `Time (${startLabel} - ${endLabel})` : 
        'Time';
    
    let traces = [];
    
    if (signal === 'TEMP') {
        // I dati TEMP arrivano già convertiti dal backend (/ 100)
        traces.push({
            y: chartData.y[0],
            x: xIndices,
            type: 'scatter',
            mode: 'lines+markers',
            line: { color: '#f59e0b', width: 2 },
            marker: { size: 4, color: '#f59e0b' },
            name: 'Temperature',
            hovertemplate: 'Index: %{x}<br>Temp: %{y:.1f}°C<extra></extra>'
        });
    } else if (signal === 'ECG') {
        traces.push({
            y: chartData.y[0],
            x: xIndices,
            type: 'scatter',
            mode: 'lines',
            line: { color: '#10b981', width: 1 },
            name: 'ECG',
            hovertemplate: 'Index: %{x}<br>Value: %{y}<extra></extra>'
        });
    } else if (signal === 'ADC') {
        chartData.y.forEach((channelData, i) => {
            traces.push({
                y: channelData,
                x: xIndices,
                type: 'scatter',
                mode: 'lines',
                line: { color: colors[i % colors.length], width: 1 },
                name: `Channel ${i + 1}`,
                hovertemplate: `Index: %{x}<br>CH${i+1}: %{y}<extra></extra>`
            });
        });
    }
    
    const layout = {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: '#0f172a',
        font: { 
            color: '#f8fafc',
            family: 'Inter, sans-serif',
            size: 11
        },
        xaxis: { 
            gridcolor: '#1e293b',
            title: timeRange,
            showline: true,
            linecolor: '#334155',
            zeroline: false
        },
        yaxis: { 
            gridcolor: '#1e293b',
            title: signal === 'TEMP' ? 'Temperature (°C)' : 'Value',
            showline: true,
            linecolor: '#334155',
            zeroline: false
        },
        margin: { l: 60, r: 30, t: 20, b: 50 },
        hovermode: 'closest',
        showlegend: signal === 'ADC' || signal === 'TEMP',
        legend: {
            x: 1,
            xanchor: 'right',
            y: 1,
            bgcolor: 'rgba(30, 41, 59, 0.9)',
            bordercolor: '#334155',
            borderwidth: 1,
            font: { size: 10 }
        }
    };
    
    const config = { 
        responsive: true,
        displayModeBar: true,
        modeBarButtonsToRemove: ['lasso2d', 'select2d'],
        displaylogo: false
    };
    
    Plotly.newPlot('historyChart', traces, layout, config);
}

// ========== NASCONDI DATI STORICI ==========
function hideHistoryData() {
    document.getElementById('historyStatsGrid').style.display = 'none';
    document.getElementById('historyChartsContainer').style.display = 'none';
    document.getElementById('emptyHistoryState').style.display = 'block';
    
    // Reset variabili
    totalDataCount = 0;
    maxSliderPosition = 0;
    currentPosition = 0;
}

// ========== MODAL MESSAGGI (RIUTILIZZA MODAL ESISTENTE) ==========
// ========== MESSAGE MODAL ==========
function showMessageModal(message, isWarning = true) {
    console.log('[MESSAGE MODAL] Called with:', message, 'isWarning:', isWarning);
    
    const modal = document.getElementById('messageModal');
    console.log('[MESSAGE MODAL] Modal element:', modal);
    
    if (!modal) {
        console.error('[MESSAGE MODAL] Modal not found! Using alert fallback');
        alert(message);
        return;
    }
    
    const messageText = document.getElementById('messageModalText');
    const modalIcon = modal.querySelector('.modal-icon svg');
    
    console.log('[MESSAGE MODAL] messageText:', messageText);
    console.log('[MESSAGE MODAL] modalIcon:', modalIcon);
    
    // Set message
    if (messageText) {
        messageText.textContent = message;
    }
    
    // Set icon based on type
    if (modalIcon) {
        if (isWarning) {
            modalIcon.innerHTML = `
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                <line x1="12" y1="9" x2="12" y2="13"/>
                <line x1="12" y1="17" x2="12.01" y2="17"/>
            `;
        } else {
            modalIcon.innerHTML = `
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="12"/>
                <line x1="12" y1="16" x2="12.01" y2="16"/>
            `;
        }
    }
    
    // Show modal 
    console.log('[MESSAGE MODAL] Adding show and active classes...');
    modal.classList.add('show', 'active');
    console.log('[MESSAGE MODAL] Modal classes:', modal.className);
}

function closeMessageModal() {
    console.log('[MESSAGE MODAL] Closing...');
    const modal = document.getElementById('messageModal');
    if (!modal) return;
    
    modal.classList.remove('show', 'active');
    console.log('[MESSAGE MODAL] Closed');
}

// Close modal on overlay click
document.addEventListener('DOMContentLoaded', function() {
    console.log('[MESSAGE MODAL] DOM loaded, setting up event listeners');
    const messageModal = document.getElementById('messageModal');
    if (messageModal) {
        console.log('[MESSAGE MODAL] Found modal, adding click listener');
        messageModal.addEventListener('click', function(e) {
            if (e.target === messageModal) {
                closeMessageModal();
            }
        });
    } else {
        console.error('[MESSAGE MODAL] Modal not found in DOM!');
    }
});

