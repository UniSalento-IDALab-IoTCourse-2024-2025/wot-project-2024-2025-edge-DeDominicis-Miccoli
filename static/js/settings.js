// ========== SETTINGS - SERIAL PORTS CONFIGURATION ==========

let availablePorts = [];
let currentConfig = null;

// Load settings when view is shown
function showSettings() {
    document.querySelectorAll('.realtime-view, .history-view, .anomalies-view, .expert-view, .settings-view, .simulate-anomaly-view').forEach(view => {
        view.classList.remove('active');
    });
    document.getElementById('settingsView').classList.add('active');
    updateSidebarActive('settings');
    
    // Attach event handlers EVERY time settings opens (to be sure they exist)
    attachSettingsEventHandlers();
    
    // Load current configuration and available ports
    loadCurrentSerialConfig();
    refreshSerialPorts();
}

// Attach event handlers to buttons
function attachSettingsEventHandlers() {
    const refreshBtn = document.getElementById('refreshPortsBtn');
    const saveBtn = document.getElementById('saveConfigBtn');
    
    if (refreshBtn && !refreshBtn._handlerAttached) {
        refreshBtn.onclick = refreshSerialPorts;
        refreshBtn._handlerAttached = true;
        console.log('[Settings] Refresh button handler attached');
    }
    
    if (saveBtn && !saveBtn._handlerAttached) {
        saveBtn.onclick = saveSerialPortsConfig;
        saveBtn._handlerAttached = true;
        console.log('[Settings] Save button handler attached');
    }
}

async function loadCurrentSerialConfig() {
    try {
        const response = await fetch('/api/serial-ports/config');
        const data = await response.json();
        
        if (data.success) {
            currentConfig = data.config;
            document.getElementById('currentShellPort').textContent = data.config.shell_port;
            document.getElementById('currentDataPort').textContent = data.config.data_port;
        } else {
            document.getElementById('currentShellPort').textContent = 'Non configurato';
            document.getElementById('currentDataPort').textContent = 'Non configurato';
        }
    } catch (error) {
        console.error('[Settings] Error loading current config:', error);
        document.getElementById('currentShellPort').textContent = 'Errore';
        document.getElementById('currentDataPort').textContent = 'Errore';
    }
}

async function refreshSerialPorts() {
    const shellSelect = document.getElementById('shellPortSelect');
    const dataSelect = document.getElementById('dataPortSelect');
    const refreshBtn = document.getElementById('refreshPortsBtn');
    
    // Check if elements exist
    if (!shellSelect || !dataSelect || !refreshBtn) {
        console.warn('[Settings] Elements not ready yet, skipping refresh');
        return;
    }
    
    // Disable button and show loading
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg> Rilevamento...';
    
    shellSelect.innerHTML = '<option value="">Caricamento...</option>';
    dataSelect.innerHTML = '<option value="">Caricamento...</option>';
    
    try {
        const response = await fetch('/api/serial-ports/detect');
        const data = await response.json();
        
        if (response.ok && data.success) {
            availablePorts = data.ports || [];
            
            if (availablePorts.length === 0) {
                shellSelect.innerHTML = '<option value="">Nessuna porta rilevata</option>';
                dataSelect.innerHTML = '<option value="">Nessuna porta rilevata</option>';
            } else {
                // Clear and populate dropdowns
                shellSelect.innerHTML = '<option value="">-- Seleziona Shell Port --</option>';
                dataSelect.innerHTML = '<option value="">-- Seleziona Data Port --</option>';
                
                availablePorts.forEach(port => {
                    const optionShell = document.createElement('option');
                    optionShell.value = port.device;
                    optionShell.textContent = `${port.device} - ${port.description}`;
                    shellSelect.appendChild(optionShell);
                    
                    const optionData = document.createElement('option');
                    optionData.value = port.device;
                    optionData.textContent = `${port.device} - ${port.description}`;
                    dataSelect.appendChild(optionData);
                });
                
                // Select current config if available
                if (currentConfig) {
                    shellSelect.value = currentConfig.shell_port;
                    dataSelect.value = currentConfig.data_port;
                }
            }
            
            console.log(`[Settings] Found ${availablePorts.length} USB ports`);
        } else {
            throw new Error(data.error || 'Failed to detect ports');
        }
    } catch (error) {
        console.error('[Settings] Error detecting ports:', error);
        shellSelect.innerHTML = '<option value="">Errore rilevamento</option>';
        dataSelect.innerHTML = '<option value="">Errore rilevamento</option>';
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
    console.log('[Settings] saveSerialPortsConfig called!');
    
    const shellPort = document.getElementById('shellPortSelect').value;
    const dataPort = document.getElementById('dataPortSelect').value;
    const saveBtn = document.getElementById('saveConfigBtn');
    const resultDiv = document.getElementById('settingsResult');
    
    console.log('[Settings] Shell Port:', shellPort);
    console.log('[Settings] Data Port:', dataPort);
    
    if (!saveBtn) {
        console.error('[Settings] Save button not found');
        showSettingsResult('error', 'Errore: bottone salvataggio non trovato');
        return;
    }
    
    // Validation
    if (!shellPort || !dataPort) {
        console.warn('[Settings] Validation failed: missing ports');
        showSettingsResult('error', 'Seleziona entrambe le porte prima di salvare');
        return;
    }
    
    if (shellPort === dataPort) {
        console.warn('[Settings] Validation failed: same ports');
        showSettingsResult('error', 'Shell Port e Data Port devono essere diverse');
        return;
    }
    
    // Disable button
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg> Salvataggio...';
    
    try {
        console.log('[Settings] Sending POST request...');
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
        console.log('[Settings] Response:', data);
        
        if (response.ok && data.success) {
            showSettingsResult('success', data.message + ' Riavvia IITdata_acq.py manualmente.');
            
            // Update current config display
            document.getElementById('currentShellPort').textContent = shellPort;
            document.getElementById('currentDataPort').textContent = dataPort;
            currentConfig = data.config;
            
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
    if (!resultDiv) {
        console.error('[Settings] Result div not found!');
        return;
    }
    
    resultDiv.className = `settings-result ${type}`;
    resultDiv.textContent = message;
    resultDiv.style.display = 'block';
    
    console.log(`[Settings] Showing result: ${type} - ${message}`);
    
    // Auto-hide after 5 seconds
    setTimeout(() => {
        resultDiv.style.display = 'none';
    }, 5000);
}

// Try to attach handlers on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    console.log('[Settings] DOMContentLoaded - trying to attach handlers');
    attachSettingsEventHandlers();
});

// Also try after a delay (fallback)
setTimeout(() => {
    console.log('[Settings] Delayed attachment - trying to attach handlers');
    attachSettingsEventHandlers();
}, 1000);