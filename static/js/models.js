// ========== MODEL MANAGEMENT WITH CHECKBOXES ==========

let currentModelType = 'ecg';
let availableModels = [];
let selectedModelFolder = null;
let activeModelData = null;

function showModelsView() {
    // Hide all views
    document.querySelectorAll('.realtime-view, .history-view, .anomalies-view, .expert-view, .settings-view, .simulate-anomaly-view, .debug-view, .models-view').forEach(view => {
        view.classList.remove('active');
    });
    
    document.getElementById('modelsView').classList.add('active');
    updateSidebarActive('models');
    
    loadModels(currentModelType);
}

function switchModelType(type) {
    currentModelType = type;
    
    document.querySelectorAll('.models-tab').forEach(tab => {
        if (tab.dataset.type === type) {
            tab.classList.add('active');
        } else {
            tab.classList.remove('active');
        }
    });
    
    loadModels(type);
}

async function loadModels(type) {
    try {
        const [modelsResponse, activeResponse] = await Promise.all([
            fetch(`/api/models/${type}/list`),
            fetch(`/api/models/${type}/active`)
        ]);
        
        const modelsData = await modelsResponse.json();
        const activeData = await activeResponse.json();
        
        if (modelsData.success) {
            availableModels = modelsData.models;
            
            if (activeData.success) {
                activeModelData = activeData.active_model;
            }
            
            renderModelsList(modelsData.models, activeData.success ? activeData.active_model : null);
        } else {
            showError('Errore nel caricamento dei modelli');
        }
    } catch (error) {
        console.error('Error loading models:', error);
        showError('Impossibile connettersi al server');
    }
}

function renderModelsList(models, activeModel) {
    const listContainer = document.getElementById('modelsList');
    
    if (models.length === 0) {
        listContainer.innerHTML = `
            <div class="empty-models">
                <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M9 3h6l3 3v13a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V6l3-3z"/>
                </svg>
                <p>Nessun modello disponibile</p>
            </div>
        `;
        return;
    }
    
    listContainer.innerHTML = models.map(model => {
        const isActive = model.is_active;
        
        // Extract date from folder name (e.g., ecg_model_v1_20250129 -> 2025-01-29)
        let displayDate = model.created_date;
        if (!displayDate || displayDate === 'N/A') {
            const match = model.folder.match(/(\d{8})$/);
            if (match) {
                const dateStr = match[1];
                displayDate = `${dateStr.slice(0,4)}-${dateStr.slice(4,6)}-${dateStr.slice(6,8)}`;
            } else {
                displayDate = 'N/A';
            }
        }
        
        return `
            <div class="model-item ${isActive ? 'active' : ''}">
                <label class="model-radio-label">
                    <input type="radio" 
                           name="selectedModel" 
                           value="${model.folder}" 
                           ${isActive ? 'checked' : ''}
                           onchange="selectModel('${model.folder}')">
                    <span class="model-radio-custom"></span>
                    <div class="model-info">
                        <div class="model-header">
                            <h4>${model.name}</h4>
                            ${isActive ? '<span class="badge-active">Attivo</span>' : ''}
                        </div>
                        <p class="model-description">${model.description}</p>
                        <div class="model-meta">
                            <span class="model-version">v${model.version}</span>
                            <span class="model-date">📅 ${displayDate}</span>
                            ${isActive ? `<span class="model-threshold">Soglia: ${activeModel.threshold.toFixed(3)}</span>` : ''}
                        </div>
                        <button class="btn-model-details" onclick="event.preventDefault(); showModelDetails('${model.folder}', '${model.name.replace(/'/g, "\\'")}')">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                <polyline points="14 2 14 8 20 8"/>
                                <line x1="12" y1="18" x2="12" y2="12"/>
                                <line x1="9" y1="15" x2="15" y2="15"/>
                            </svg>
                            Visualizza Dettagli
                        </button>
                    </div>
                </label>
            </div>
        `;
    }).join('');
    
    // Set initial selection
    if (activeModel) {
        selectedModelFolder = activeModel.folder;
        updateThresholdSlider(activeModel.threshold);
        document.getElementById('btnApplyModel').disabled = true;
        document.getElementById('modelThresholdSlider').disabled = false;
    }
}

function selectModel(folder) {
    selectedModelFolder = folder;
    
    // Enable threshold slider and apply button
    const slider = document.getElementById('modelThresholdSlider');
    const applyBtn = document.getElementById('btnApplyModel');
    
    slider.disabled = false;
    applyBtn.disabled = false;
    
    // Set threshold to active model's threshold if this is the active model
    if (activeModelData && activeModelData.folder === folder) {
        updateThresholdSlider(activeModelData.threshold);
    } else {
        // Use default threshold
        updateThresholdSlider(currentModelType === 'ecg' ? 0.1 : 0.15);
    }
}

function updateThresholdSlider(value) {
    const slider = document.getElementById('modelThresholdSlider');
    const display = document.getElementById('modelThresholdDisplay');
    
    slider.value = value;
    display.textContent = value.toFixed(2);
}

// Update display on slider change
document.addEventListener('DOMContentLoaded', () => {
    const slider = document.getElementById('modelThresholdSlider');
    if (slider) {
        slider.addEventListener('input', (e) => {
            document.getElementById('modelThresholdDisplay').textContent = parseFloat(e.target.value).toFixed(2);
            document.getElementById('btnApplyModel').disabled = false;
        });
    }
});

async function applyModelSelection() {
    if (!selectedModelFolder) {
        alert('Seleziona un modello');
        return;
    }
    
    const threshold = parseFloat(document.getElementById('modelThresholdSlider').value);
    
    try {
        const response = await fetch(`/api/models/${currentModelType}/activate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model_folder: selectedModelFolder,
                threshold: threshold
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showSuccess('Configurazione applicata con successo');
            
            // Reload models to update UI
            setTimeout(() => loadModels(currentModelType), 500);
        } else {
            showError(`Errore: ${data.error}`);
        }
    } catch (error) {
        console.error('Error applying model:', error);
        showError('Errore di connessione');
    }
}

function showSuccess(message) {
    // Temporary - could use notification system
    const btn = document.getElementById('btnApplyModel');
    const originalText = btn.innerHTML;
    btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg> ${message}`;
    btn.style.background = '#10b981';
    setTimeout(() => {
        btn.innerHTML = originalText;
        btn.style.background = '';
    }, 2000);
}

function showError(message) {
    alert(message);
}
// ========== MODEL DETAILS MODAL ==========

async function showModelDetails(folder, modelName) {
    const modal = document.getElementById('modelDetailsModal');
    const title = document.getElementById('modelDetailsTitle');
    const gallery = document.getElementById('modelImagesGallery');
    
    // Set title
    title.textContent = `Dettagli: ${modelName}`;
    
    // Show modal with loading
    modal.classList.add('show');
    gallery.innerHTML = `
        <div class="loading-spinner">
            <div class="spinner"></div>
            <p>Caricamento immagini...</p>
        </div>
    `;
    
    try {
        // Fetch images list
        const response = await fetch(`/api/models/${currentModelType}/${folder}/images`);
        const data = await response.json();
        
        if (data.success && data.images.length > 0) {
            renderModelImages(data.images, folder);
        } else {
            gallery.innerHTML = `
                <div class="no-images">
                    <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                        <circle cx="8.5" cy="8.5" r="1.5"/>
                        <polyline points="21 15 16 10 5 21"/>
                    </svg>
                    <p>Nessuna immagine disponibile per questo modello</p>
                </div>
            `;
        }
    } catch (error) {
        console.error('Error loading model images:', error);
        gallery.innerHTML = `
            <div class="error-message">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/>
                    <line x1="15" y1="9" x2="9" y2="15"/>
                    <line x1="9" y1="9" x2="15" y2="15"/>
                </svg>
                <p>Errore nel caricamento delle immagini</p>
            </div>
        `;
    }
}

function renderModelImages(images, folder) {
    const gallery = document.getElementById('modelImagesGallery');
    
    gallery.innerHTML = images.map(img => `
        <div class="model-image-card">
            <h4 class="model-image-title">${img.title}</h4>
            <div class="model-image-container">
                <img src="${img.url}" 
                     alt="${img.title}" 
                     onclick="openImageFullscreen('${img.url}', '${img.title}')"
                     loading="lazy">
            </div>
        </div>
    `).join('');
}

function openImageFullscreen(url, title) {
    const overlay = document.createElement('div');
    overlay.className = 'fullscreen-image-overlay';
    overlay.onclick = () => overlay.remove();
    
    overlay.innerHTML = `
        <div class="fullscreen-image-container">
            <div class="fullscreen-image-header">
                <h3>${title}</h3>
                <button onclick="this.closest('.fullscreen-image-overlay').remove()">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="18" y1="6" x2="6" y2="18"/>
                        <line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>
            <img src="${url}" alt="${title}" onclick="event.stopPropagation()">
        </div>
    `;
    
    document.body.appendChild(overlay);
}

function closeModelDetails() {
    document.getElementById('modelDetailsModal').classList.remove('show');
}

// Export to window for onclick
window.showModelDetails = showModelDetails;
window.closeModelDetails = closeModelDetails;
window.openImageFullscreen = openImageFullscreen;