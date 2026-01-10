
from flask import jsonify, request
from pathlib import Path
import json
from datetime import datetime

# Directory base modelli
MODELS_BASE_DIR = Path("models")

@app.route('/api/models/<model_type>/list', methods=['GET'])
def list_models(model_type):
    """
    Lista tutti i modelli disponibili per un tipo (ecg, piezo)
    
    Returns:
        {
            "success": true,
            "models": [
                {
                    "folder": "ecg_model_v1_20250129",
                    "name": "ECG Anomaly Detector",
                    "version": "1.0",
                    "description": "...",
                    "is_active": true
                }
            ]
        }
    """
    try:
        model_dir = MODELS_BASE_DIR / model_type
        if not model_dir.exists():
            return jsonify({"success": False, "error": f"Model type '{model_type}' not found"}), 404
        
        # Leggi modello attivo
        active_file = model_dir / "active_model.json"
        active_folder = None
        if active_file.exists():
            with open(active_file, 'r') as f:
                active_data = json.load(f)
                active_folder = active_data.get('model_folder')
        
        # Scansiona cartelle modelli
        models = []
        for folder in model_dir.iterdir():
            if folder.is_dir():
                config_file = folder / "config.json"
                if config_file.exists():
                    with open(config_file, 'r') as f:
                        config = json.load(f)
                    
                    models.append({
                        "folder": folder.name,
                        "name": config.get("name", "Unknown"),
                        "version": config.get("version", "1.0"),
                        "description": config.get("description", ""),
                        "created_date": config.get("created_date", "N/A"),
                        "is_active": folder.name == active_folder
                    })
        
        # Ordina per versione (decrescente)
        models.sort(key=lambda x: x['version'], reverse=True)
        
        return jsonify({
            "success": True,
            "models": models
        })
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/models/<model_type>/active', methods=['GET'])
def get_active_model(model_type):
    """
    Ottieni configurazione del modello attivo
    
    Returns:
        {
            "success": true,
            "active_model": {
                "folder": "ecg_model_v1_20250129",
                "threshold": 0.1,
                "selected_date": "2025-01-29T16:52:00",
                "config": {
                    "name": "...",
                    "version": "...",
                    "description": "..."
                }
            }
        }
    """
    try:
        model_dir = MODELS_BASE_DIR / model_type
        active_file = model_dir / "active_model.json"
        
        if not active_file.exists():
            return jsonify({"success": False, "error": "No active model configured"}), 404
        
        with open(active_file, 'r') as f:
            active_data = json.load(f)
        
        # Leggi config del modello
        model_folder = active_data.get('model_folder')
        config_file = model_dir / model_folder / "config.json"
        
        if config_file.exists():
            with open(config_file, 'r') as f:
                config = json.load(f)
        else:
            config = {}
        
        return jsonify({
            "success": True,
            "active_model": {
                "folder": model_folder,
                "threshold": active_data.get('threshold', 0.1),
                "selected_date": active_data.get('selected_date'),
                "config": config
            }
        })
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/models/<model_type>/activate', methods=['POST'])
def activate_model(model_type):
    """
    Attiva un modello specifico con una soglia
    
    Request body:
        {
            "model_folder": "ecg_model_v2_20250130",
            "threshold": 0.085
        }
    
    Returns:
        {
            "success": true,
            "message": "Model activated successfully"
        }
    """
    try:
        data = request.get_json()
        model_folder = data.get('model_folder')
        threshold = data.get('threshold')
        
        if not model_folder or threshold is None:
            return jsonify({"success": False, "error": "Missing model_folder or threshold"}), 400
        
        model_dir = MODELS_BASE_DIR / model_type
        target_model_dir = model_dir / model_folder
        
        # Verifica che il modello esista
        if not target_model_dir.exists():
            return jsonify({"success": False, "error": f"Model folder '{model_folder}' not found"}), 404
        
        # Verifica che abbia config.json
        config_file = target_model_dir / "config.json"
        if not config_file.exists():
            return jsonify({"success": False, "error": "Model config.json not found"}), 404
        
        # Aggiorna active_model.json
        active_file = model_dir / "active_model.json"
        active_data = {
            "model_folder": model_folder,
            "threshold": float(threshold),
            "selected_date": datetime.now().isoformat()
        }
        
        with open(active_file, 'w') as f:
            json.dump(active_data, f, indent=2)
        
        return jsonify({
            "success": True,
            "message": f"Model '{model_folder}' activated with threshold {threshold}"
        })
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/models/<model_type>/config', methods=['GET'])
def get_model_config(model_type):
    """
    Ottieni config.json di un modello specifico
    
    Query params:
        ?folder=ecg_model_v1_20250129
    
    Returns:
        {
            "success": true,
            "config": {
                "name": "...",
                "version": "...",
                "description": "..."
            }
        }
    """
    try:
        model_folder = request.args.get('folder')
        if not model_folder:
            return jsonify({"success": False, "error": "Missing 'folder' query parameter"}), 400
        
        model_dir = MODELS_BASE_DIR / model_type / model_folder
        config_file = model_dir / "config.json"
        
        if not config_file.exists():
            return jsonify({"success": False, "error": "Config file not found"}), 404
        
        with open(config_file, 'r') as f:
            config = json.load(f)
        
        return jsonify({
            "success": True,
            "config": config
        })
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500