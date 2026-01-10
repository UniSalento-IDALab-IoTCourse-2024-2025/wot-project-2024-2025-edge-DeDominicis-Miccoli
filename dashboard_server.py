"""
Dashboard Server for IIT Device Data Acquisition
Con supporto per anomalie ECG, PIEZO e TEMPERATURE + Sistema Notifiche Real-time
"""
from flask import Flask, render_template, jsonify, request, send_file
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import threading
import time
import secrets
import os
import json
from collections import deque
from datetime import datetime
from pathlib import Path

from functools import wraps
from flask import send_from_directory, redirect, url_for
from auth_db import AuthDB


# Import storage module
from data_storage import get_storage_instance

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

CORS(app)

# Initialize authentication database
auth_db = AuthDB('users.db')

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Global state
class DashboardState:
    def __init__(self):
        self.is_acquiring = False
        self.device_connected = False
        self.data_queues = {
            'ECG': deque(maxlen=2500),
            'ADC': deque(maxlen=2500),
            'TEMP': deque(maxlen=120)
        }
        self.stats = {
            'ECG': {'samples': 0, 'last_update': None},
            'ADC': {'samples': 0, 'last_update': None},
            'TEMP': {'samples': 0, 'last_update': None, 'current_temp': None}
        }
        self.start_time = None
        self.packet_count = 0
        self.current_session_id = None
        
        # System logs buffer (last 1000 log entries)
        self.system_logs = deque(maxlen=1000)
        
        # Notification tracking - initialize with existing anomaly counts to prevent spam
        self.last_notification_counts = self._get_initial_anomaly_counts()
    
    def _get_initial_anomaly_counts(self):
        """
        Count existing anomalies on startup to avoid re-notifying them
        
        Returns:
            dict: Anomaly counts for each type (ecg, piezo, temp)
        """
        counts = {'ecg': 0, 'piezo': 0, 'temp': 0}
        
        anomaly_dir = Path("anomaly_logs")
        if not anomaly_dir.exists():
            print("[Startup] No anomaly_logs directory found")
            return counts
        
        today = datetime.now().strftime("%Y%m%d")
        
        # File da controllare
        files = {
            'ecg': anomaly_dir / f"anomalies_{today}.json",
            'piezo': anomaly_dir / f"piezo_anomalies_{today}.json",
            'temp': anomaly_dir / f"temp_anomalies_{today}.json"
        }
        
        for anomaly_type, file_path in files.items():
            if file_path.exists():
                try:
                    with open(file_path, 'r') as f:
                        anomalies = json.load(f)
                    counts[anomaly_type] = len(anomalies) if isinstance(anomalies, list) else 0
                    print(f"[Startup] Found {counts[anomaly_type]} existing {anomaly_type.upper()} anomalies")
                except Exception as e:
                    print(f"[Startup] Error reading {anomaly_type} anomalies: {e}")
                    counts[anomaly_type] = 0
        
        print(f"[Startup] Total existing anomalies: ECG={counts['ecg']}, PIEZO={counts['piezo']}, TEMP={counts['temp']}")
        return counts
        
state = DashboardState()
storage = get_storage_instance()


# ========== AUTHENTICATION DECORATORS ==========

def require_auth(f):
    """Decorator to require authentication for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if token and token.startswith('Bearer '):
            token = token[7:]  # Remove "Bearer "
        else:
            token = request.cookies.get('session_token')
        
        result = auth_db.verify_session(token)
        if not result['success']:
            return jsonify({'success': False, 'error': 'Non autorizzato'}), 401
        
        request.current_user = result['user']
        return f(*args, **kwargs)
    
    return decorated_function

def require_admin(f):
    """Decorator to require admin role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if token and token.startswith('Bearer '):
            token = token[7:]
        else:
            token = request.cookies.get('session_token')
        
        result = auth_db.verify_session(token)
        if not result['success']:
            return jsonify({'success': False, 'error': 'Non autorizzato'}), 401
        
        if result['user']['ruolo'] != 'admin' :
            return jsonify({'success': False, 'error': 'Accesso negato - solo admin'}), 403
        
        request.current_user = result['user']
        return f(*args, **kwargs)
    
    return decorated_function

def require_medico_or_admin(f):
    """Decorator to require medico or admin role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if token and token.startswith('Bearer '):
            token = token[7:]
        else:
            token = request.cookies.get('session_token')
        
        result = auth_db.verify_session(token)
        if not result['success']:
            return jsonify({'success': False, 'error': 'Non autorizzato'}), 401
        
        user_role = result['user']['ruolo']
        
        # Permetti solo medico e admin
        if user_role not in ['medico', 'admin']:
            return jsonify({'success': False, 'error': 'Accesso negato - solo medico o admin'}), 403
        
        request.current_user = result['user']
        return f(*args, **kwargs)
    
    return decorated_function


# ====== NOTIFICATION SYSTEM ======

def send_anomaly_notification(anomaly_type: str, anomaly_data: dict):
    """
    Invia notifica real-time quando viene rilevata una nuova anomalia
    
    Args:
        anomaly_type: 'ecg', 'piezo', o 'temp'
        anomaly_data: Dati dell'anomalia
    """
    notification = {
        'type': anomaly_type,
        'timestamp': datetime.now().isoformat(),
        'data': anomaly_data
    }
    
    # DEBUG: Stampa cosa stiamo inviando
    print(f"[Notification] Sending {anomaly_type.upper()} notification to /data namespace")
    print(f"[Notification] Data: {notification}")
    
    # Invia via SocketIO a tutti i client connessi
    socketio.emit('new_anomaly', notification, namespace='/data')
    
    # PROVA ANCHE SENZA NAMESPACE (broadcast globale)
    socketio.emit('new_anomaly', notification)
    
    print(f"[Notification] Notification sent successfully")


def check_for_new_anomalies():
    """
    Controlla periodicamente i file di log per nuove anomalie
    e invia notifiche quando ne trova
    """
    anomaly_dir = Path("anomaly_logs")
    if not anomaly_dir.exists():
        return
    
    today = datetime.now().strftime("%Y%m%d")
    
    # File da monitorare
    files_to_check = {
        'ecg': anomaly_dir / f"anomalies_{today}.json",
        'piezo': anomaly_dir / f"piezo_anomalies_{today}.json",
        'temp': anomaly_dir / f"temp_anomalies_{today}.json"
    }
    
    for anomaly_type, file_path in files_to_check.items():
        if not file_path.exists():
            continue
        
        try:
            with open(file_path, 'r') as f:
                anomalies = json.load(f)
            
            current_count = len(anomalies) if isinstance(anomalies, list) else 0
            last_count = state.last_notification_counts[anomaly_type]
            
            # Se ci sono nuove anomalie
            if current_count > last_count:
                # Invia notifica per ogni nuova anomalia
                for i in range(last_count, current_count):
                    anomaly = anomalies[i]
                    #Altrimenti arriva una notifica doppia
                    #send_anomaly_notification(anomaly_type, anomaly)
                
                # Aggiorna contatore
                state.last_notification_counts[anomaly_type] = current_count
                
        except Exception as e:
            app.logger.error(f"Error checking {anomaly_type} anomalies: {str(e)}")


# ====== VALIDAZIONE INPUT ======

def validate_signal_name(signal):
    """Valida il nome del segnale"""
    allowed_signals = ['ECG', 'ADC', 'TEMP']
    return signal in allowed_signals

def validate_session_id(session_id):
    """Valida il formato del session ID"""
    if not session_id or len(session_id) != 15:
        return False
    try:
        datetime.strptime(session_id, '%Y%m%d_%H%M%S')
        return True
    except:
        return False

def validate_date_string(date_str):
    """Valida il formato della data"""
    if not date_str or len(date_str) != 8:
        return False
    try:
        datetime.strptime(date_str, '%Y%m%d')
        return True
    except:
        return False

def validate_window_params(position, window_size, total_count):
    """Valida parametri di paginazione"""
    if position < 0 or window_size < 1:
        return False
    if position >= total_count and total_count > 0:
        return False
    return True

# ====== FUNZIONI DATI ======

def push_data(signal_name, frames, timestamp=None):
    """Push new data frames to the dashboard"""
    if not validate_signal_name(signal_name):
        return
    
    for frame in frames:
        state.data_queues[signal_name].append({
            'values': frame,
            'timestamp': timestamp or time.time()
        })
    
    state.stats[signal_name]['samples'] += len(frames)
    state.stats[signal_name]['last_update'] = datetime.now().isoformat()
    
    if signal_name == 'TEMP' and frames:
        state.stats[signal_name]['current_temp'] = frames[-1][0]
    
    state.packet_count += 1
    
    if state.packet_count % 5 == 0:
        socketio.emit('data_update', {
            'signal': signal_name,
            'data': prepare_chart_data(signal_name)
        }, namespace='/data')

def prepare_chart_data(signal_name, max_points=1000):
    """Prepara i dati per il grafico con downsampling intelligente"""
    if not validate_signal_name(signal_name):
        return {'x': [], 'y': []}
    
    data = list(state.data_queues[signal_name])
    
    if len(data) == 0:
        return {'x': [], 'y': []}
    
    # Downsampling se necessario
    if len(data) > max_points:
        step = len(data) // max_points
        data = data[::step]
    
    if signal_name == 'TEMP':
        return {
            'x': list(range(len(data))),
            'y': [[d['values'][0]] for d in data]
        }
    else:
        num_channels = len(data[0]['values']) if data else 0
        y_data = [[] for _ in range(num_channels)]
        
        for point in data:
            for ch in range(num_channels):
                y_data[ch].append(point['values'][ch])
        
        return {
            'x': list(range(len(data))),
            'y': y_data
        }

# ====== PAGINAZIONE DATI STORICI ======

def get_windowed_historical_data(session_id, signal, position, window_size):
    """
    Ottieni una finestra di dati storici con logica di paginazione sul backend
    """
    if not validate_session_id(session_id) or not validate_signal_name(signal):
        return None
    
    try:
        all_data = storage.load_session_data(session_id, signal, limit=None)
        
        if not all_data:
            return {
                'data': {'x': [], 'y': []},
                'count': 0,
                'window_start': 0,
                'window_end': 0,
                'total_count': 0
            }
        
        total_count = len(all_data)
        
        if window_size == -1:
            start = 0
            end = total_count
        else:
            start = max(0, min(position, total_count - 1))
            end = min(total_count, start + window_size)
        
        windowed_data = all_data[start:end]
        
        if signal == 'TEMP':
            y_data = [[point['values'][0] / 100 for point in windowed_data]]
        else:
            num_channels = len(windowed_data[0]['values']) if windowed_data else 0
            y_data = [[] for _ in range(num_channels)]
            
            for point in windowed_data:
                for ch in range(num_channels):
                    y_data[ch].append(point['values'][ch])
        
        return {
            'data': {
                'x': list(range(start, end)),
                'y': y_data
            },
            'count': len(windowed_data),
            'window_start': start,
            'window_end': end,
            'total_count': total_count,
            'max_position': max(0, total_count - window_size) if window_size > 0 else 0
        }
    
    except Exception as e:
        app.logger.error(f"Errore nella paginazione dati: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        return None

# ========== AUTHENTICATION ROUTES & HANDLERS ==========

@app.before_request
def check_authentication():
    """Check authentication before each request"""
    public_routes = ['/login', '/register', '/api/auth/login', '/api/auth/register', '/static', '/api/models/upload']    
    # Allow public routes
    if any(request.path.startswith(route) for route in public_routes):
        return None
    
    # Root redirect to login
    if request.path == '/':
        return redirect('/login')
    
    # Check session for protected routes
    token = request.cookies.get('session_token')
    if not token:
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'Non autorizzato'}), 401
        else:
            return redirect('/login')
    
    result = auth_db.verify_session(token)
    if not result['success']:
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'Sessione scaduta'}), 401
        else:
            return redirect('/login')
    
    request.current_user = result['user']
    return None

@app.route('/login')
def login_page():
    """Login page"""
    return send_from_directory('templates', 'login.html')

@app.route('/register')
def register_page():
    """Registration page"""
    return send_from_directory('templates', 'register.html')

@app.route('/dashboard')
@require_auth
def dashboard_page():
    """Main dashboard - requires authentication"""
    return render_template('dashboard.html')

@app.route('/api/auth/register', methods=['POST'])
def register():
    """User registration API"""
    data = request.json
    
    username = data.get('username')
    password = data.get('password')
    nome = data.get('nome')
    cognome = data.get('cognome')
    ruolo = data.get('ruolo')
    
    if not all([username, password, nome, cognome, ruolo]):
        return jsonify({'success': False, 'error': 'Tutti i campi sono obbligatori'}), 400
    
    result = auth_db.register_user(username, password, nome, cognome, ruolo)
    
    if result['success']:
        return jsonify(result), 201
    else:
        return jsonify(result), 400

@app.route('/api/auth/login', methods=['POST'])
def login():
    """User login API"""
    data = request.json
    
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username e password richiesti'}), 400
    
    result = auth_db.login(username, password)
    
    if result['success']:
        response = jsonify(result)
        response.set_cookie('session_token', result['session_token'], httponly=True, max_age=2400)  # 40 minutes
        return response, 200
    else:
        return jsonify(result), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """User logout API"""
    token = request.headers.get('Authorization')
    if token and token.startswith('Bearer '):
        token = token[7:]
    else:
        token = request.cookies.get('session_token')
    
    if token:
        auth_db.logout(token)
    
    response = jsonify({'success': True, 'message': 'Logout effettuato'})
    response.delete_cookie('session_token')
    return response, 200

@app.route('/api/auth/verify', methods=['GET'])
@require_auth
def verify_session():
    """Verify current session"""
    return jsonify({
        'success': True,
        'user': request.current_user
    }), 200

# ========== USER MANAGEMENT ROUTES (ADMIN ONLY) ==========

@app.route('/api/users/list', methods=['GET'])
@require_admin
def list_users():
    """List all users - admin only"""
    result = auth_db.get_all_users()
    return jsonify(result), 200

@app.route('/api/users/<int:user_id>', methods=['GET'])
@require_admin
def get_user(user_id):
    """Get user by ID - admin only"""
    result = auth_db.get_user_by_id(user_id)
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 404

@app.route('/api/users/<int:user_id>', methods=['PUT'])
@require_admin
def update_user(user_id):
    """Update user - admin only"""
    data = request.json
    
    nome = data.get('nome')
    cognome = data.get('cognome')
    ruolo = data.get('ruolo')
    new_password = data.get('new_password')
    
    result = auth_db.update_user(user_id, nome, cognome, ruolo, new_password)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@require_admin
def delete_user(user_id):
    """Delete user - admin only"""
    result = auth_db.delete_user(user_id)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400
    

# ========== SYNC ENDPOINTS ==========

# Token condiviso per sincronizzazione
SYNC_TOKEN = "test123"

def verify_sync_token():
    """Verifica token di sincronizzazione"""
    token = request.headers.get('X-Sync-Token')
    if token != SYNC_TOKEN:
        return False
    return True

@app.route('/api/users/sync', methods=['GET'])
def get_users_for_sync():
    """
    GET - Ritorna tutti gli utenti per sincronizzazione
    Headers richiesti: X-Sync-Token
    """
    if not verify_sync_token():
        return jsonify({'success': False, 'error': 'Unauthorized - Invalid sync token'}), 401
    
    try:
        users = auth_db.get_all_users_for_sync()
        
        return jsonify({
            'success': True,
            'users': users,
            'count': len(users),
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        print(f"[Sync] Error getting users: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/users/sync', methods=['POST'])
def receive_users_for_sync():
    """
    POST - Riceve utenti da sincronizzare
    Headers richiesti: X-Sync-Token
    Body: {"users": [...]}
    """
    if not verify_sync_token():
        return jsonify({'success': False, 'error': 'Unauthorized - Invalid sync token'}), 401
    
    try:
        data = request.get_json()
        users = data.get('users', [])
        
        if not users:
            return jsonify({'success': False, 'error': 'No users provided'}), 400
        
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        
        updated = 0
        inserted = 0
        conflicts = []
        
        for user in users:
            # Verifica se utente esiste
            cursor.execute("SELECT id, updated_at FROM users WHERE id = ?", (user['id'],))
            existing = cursor.fetchone()
            
            if existing:
                existing_id, existing_updated_at = existing
                
                # Confronta timestamp
                try:
                    incoming_updated = user.get('updated_at')
                    
                    if not incoming_updated or not existing_updated_at:
                        # Uno dei due non ha timestamp - skip
                        conflicts.append({
                            'id': user['id'],
                            'username': user['username'],
                            'reason': 'missing_timestamp'
                        })
                        continue
                    
                    # Parse timestamps
                    incoming_ts = datetime.fromisoformat(incoming_updated.replace('Z', '+00:00'))
                    existing_ts = datetime.fromisoformat(existing_updated_at.replace('Z', '+00:00'))
                    
                    # Confronta (con tolleranza di 1 secondo)
                    diff = abs((incoming_ts - existing_ts).total_seconds())
                    
                    if diff < 1:
                        # Stesso timestamp - già sincronizzato
                        continue
                    elif incoming_ts > existing_ts:
                        # Incoming è più recente - aggiorna
                        cursor.execute('''
                            UPDATE users 
                            SET username=?, password_hash=?, nome=?, cognome=?, ruolo=?, 
                                created_at=?, last_login=?, updated_at=?
                            WHERE id=?
                        ''', (
                            user['username'], user['password_hash'], user['nome'],
                            user['cognome'], user['ruolo'], user.get('created_at'),
                            user.get('last_login'), user['updated_at'], user['id']
                        ))
                        updated += 1
                        print(f"[Sync] ✓ Updated user {user['id']} ({user['username']}) - incoming newer")
                    else:
                        # Existing è più recente - conflitto (il remote dovrebbe pullare)
                        conflicts.append({
                            'id': user['id'],
                            'username': user['username'],
                            'reason': 'local_newer',
                            'local_ts': existing_updated_at,
                            'incoming_ts': incoming_updated
                        })
                        print(f"[Sync] ⚠ Conflict: user {user['id']} ({user['username']}) - local is newer")
                
                except Exception as ts_error:
                    print(f"[Sync] Error comparing timestamps for user {user['id']}: {ts_error}")
                    conflicts.append({
                        'id': user['id'],
                        'username': user['username'],
                        'reason': 'timestamp_parse_error'
                    })
            
            else:
                # Nuovo utente - inserisci
                cursor.execute('''
                    INSERT INTO users (id, username, password_hash, nome, cognome, ruolo, created_at, last_login, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user['id'], user['username'], user['password_hash'], user['nome'],
                    user['cognome'], user['ruolo'], user.get('created_at'),
                    user.get('last_login'), user.get('updated_at')
                ))
                inserted += 1
                print(f"[Sync] ✓ Inserted new user {user['id']} ({user['username']})")
        
        conn.commit()
        conn.close()
        
        if conflicts:
            print(f"[Sync] ⚠ {len(conflicts)} conflicts detected")
            for c in conflicts:
                print(f"  - User {c['id']} ({c['username']}): {c['reason']}")
        
        return jsonify({
            'success': True,
            'updated': updated,
            'inserted': inserted,
            'conflicts': conflicts,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        print(f"[Sync] Error receiving users: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# ========== FINE SYNC ENDPOINTS ==========

@app.route('/')
def index():
    """Pagina principale"""
    return render_template('dashboard.html')

@require_auth
@app.route('/api/status')
def get_status():
    """Ottieni lo stato del sistema"""
    return jsonify({
        'is_acquiring': state.is_acquiring,
        'device_connected': state.device_connected,
        'uptime': int(time.time() - state.start_time) if state.start_time else 0,
        'stats': state.stats,
        'packet_count': state.packet_count,
        'current_session_id': state.current_session_id
    })

@require_auth
@app.route('/api/data/<signal>')
def get_data(signal):
    """Ottieni dati per un segnale specifico"""
    if not validate_signal_name(signal):
        return jsonify({'error': 'Invalid signal name'}), 400
    
    return jsonify(prepare_chart_data(signal))

@require_auth
@app.route('/api/control/<action>', methods=['POST'])
def control_action(action):
    """Controllo acquisizione"""
    if action not in ['start', 'stop', 'reset']:
        return jsonify({'error': 'Invalid action'}), 400
    
    if action == 'start':
        state.is_acquiring = True
        socketio.emit('control_command', {'action': 'start'}, namespace='/control')
        return jsonify({'status': 'started'})
    elif action == 'stop':
        state.is_acquiring = False
        socketio.emit('control_command', {'action': 'stop'}, namespace='/control')
        return jsonify({'status': 'stopped'})
    elif action == 'reset':
        for signal in state.stats:
            state.stats[signal]['samples'] = 0
            state.stats[signal]['last_update'] = None
            if signal == 'TEMP':
                state.stats[signal]['current_temp'] = None
        
        for queue in state.data_queues.values():
            queue.clear()
        
        state.packet_count = 0
        state.start_time = None
        
        return jsonify({'status': 'reset'})

# ====== API DATI STORICI ======

@require_auth
@app.route('/api/history/sessions')
def get_sessions():
    """Ottieni lista sessioni salvate"""
    try:
        sessions = storage.get_all_sessions()
        return jsonify({
            'sessions': sessions,
            'count': len(sessions)
        })
    except Exception as e:
        app.logger.error(f"Errore nel recupero sessioni: {str(e)}")
        return jsonify({'error': 'Errore server'}), 500

@require_auth
@app.route('/api/history/sessions/<date>')
def get_sessions_by_date(date):
    """Ottieni sessioni per una data specifica"""
    if not validate_date_string(date):
        return jsonify({'error': 'Formato data non valido'}), 400
    
    try:
        sessions = storage.get_sessions_by_date(date)
        return jsonify({
            'date': date,
            'sessions': sessions,
            'count': len(sessions)
        })
    except Exception as e:
        app.logger.error(f"Errore nel recupero sessioni per data: {str(e)}")
        return jsonify({'error': 'Errore server'}), 500

@require_auth
@app.route('/api/history/data/<session_id>/<signal>')
def get_historical_data(session_id, signal):
    """Ottieni dati storici"""
    if not validate_session_id(session_id):
        return jsonify({'error': 'Session ID non valido'}), 400
    
    if not validate_signal_name(signal):
        return jsonify({'error': 'Nome segnale non valido'}), 400
    
    try:
        data_points = storage.load_session_data(session_id, signal, limit=None)
        
        if not data_points:
            return jsonify({
                'session_id': session_id,
                'signal': signal,
                'data': {'x': [], 'y': []},
                'count': 0
            })
        
        if signal == 'TEMP':
            y_data = [[point['values'][0] / 100 for point in data_points]]
        else:
            num_channels = len(data_points[0]['values']) if data_points else 0
            y_data = [[] for _ in range(num_channels)]
            
            for point in data_points:
                for ch in range(num_channels):
                    y_data[ch].append(point['values'][ch])
        
        return jsonify({
            'session_id': session_id,
            'signal': signal,
            'data': {
                'x': list(range(len(data_points))),
                'y': y_data
            },
            'count': len(data_points)
        })
    except Exception as e:
        app.logger.error(f"Errore nel caricamento dati storici: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({'error': 'Errore server'}), 500

@require_auth
@app.route('/api/history/window/<session_id>/<signal>')
def get_historical_window(session_id, signal):
    """API: Ottieni finestra di dati storici con paginazione backend"""
    if not validate_session_id(session_id):
        return jsonify({'error': 'Session ID non valido'}), 400
    
    if not validate_signal_name(signal):
        return jsonify({'error': 'Nome segnale non valido'}), 400
    
    position = request.args.get('position', default=0, type=int)
    window_size = request.args.get('window_size', default=1000, type=int)
    
    if window_size != -1 and (window_size < 100 or window_size > 50000):
        return jsonify({'error': 'window_size deve essere tra 100 e 50000, o -1 per tutti'}), 400
    
    if position < 0:
        return jsonify({'error': 'position deve essere >= 0'}), 400
    
    try:
        result = get_windowed_historical_data(session_id, signal, position, window_size)
        
        if result is None:
            return jsonify({'error': 'Errore nel recupero dati'}), 500
        
        return jsonify({
            'session_id': session_id,
            'signal': signal,
            **result
        })
    
    except Exception as e:
        app.logger.error(f"Errore nella paginazione: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({'error': 'Errore server'}), 500

@require_auth
@app.route('/api/history/dates')
def get_available_dates():
    """Ottieni lista date disponibili"""
    try:
        sessions = storage.get_all_sessions()
        
        dates = set()
        for session in sessions:
            date_part = session['session_id'].split('_')[0]
            if validate_date_string(date_part):
                dates.add(date_part)
        
        dates_list = sorted(list(dates), reverse=True)
        
        formatted_dates = []
        for date in dates_list:
            try:
                dt = datetime.strptime(date, '%Y%m%d')
                formatted_dates.append({
                    'value': date,
                    'label': dt.strftime('%d %B %Y')
                })
            except:
                continue
        
        return jsonify({
            'dates': formatted_dates,
            'count': len(formatted_dates)
        })
    except Exception as e:
        app.logger.error(f"Errore nel recupero date disponibili: {str(e)}")
        return jsonify({'error': 'Errore server'}), 500

# ====== API ANOMALIE ======

@require_auth
@app.route('/api/anomalies/dates')
def get_anomaly_dates():
    """Ottieni lista date con anomalie disponibili"""
    try:
        anomaly_dir = Path("anomaly_logs")
        if not anomaly_dir.exists():
            return jsonify({'dates': [], 'count': 0})
        
        dates_with_anomalies = set()
        
        for log_file in anomaly_dir.glob("*.json"):
            filename = log_file.stem
            date_str = None
            
            if filename.startswith("anomalies_"):
                date_str = filename.replace("anomalies_", "")
            elif filename.startswith("piezo_anomalies_"):
                date_str = filename.replace("piezo_anomalies_", "")
            elif filename.startswith("temp_anomalies_"):
                date_str = filename.replace("temp_anomalies_", "")
            else:
                continue
            
            if date_str and len(date_str) == 8 and date_str.isdigit():
                try:
                    with open(log_file, 'r') as f:
                        data = json.load(f)
                        if isinstance(data, list) and len(data) > 0:
                            dates_with_anomalies.add(date_str)
                except:
                    continue
        
        dates_list = sorted(list(dates_with_anomalies), reverse=True)
        
        formatted_dates = []
        for date in dates_list:
            try:
                dt = datetime.strptime(date, '%Y%m%d')
                formatted_dates.append({
                    'value': date,
                    'label': dt.strftime('%d %B %Y')
                })
            except:
                continue
        
        return jsonify({
            'dates': formatted_dates,
            'count': len(formatted_dates)
        })
    except Exception as e:
        app.logger.error(f"Errore nel recupero date anomalie: {str(e)}")
        return jsonify({'error': 'Errore server'}), 500


@require_auth
@app.route('/api/anomalies/data/<date>')
def get_anomalies_by_date(date):
    """Ottieni tutte le anomalie per una data specifica"""
    if not validate_date_string(date):
        return jsonify({'error': 'Formato data non valido'}), 400
    
    try:
        anomaly_dir = Path("anomaly_logs")
        if not anomaly_dir.exists():
            return jsonify({
                'date': date,
                'ecg_anomalies': [],
                'piezo_anomalies': [],
                'temp_anomalies': [],
                'total_count': 0
            })
        
        ecg_anomalies = []
        piezo_anomalies = []
        temp_anomalies = []
        
        ecg_file = anomaly_dir / f"anomalies_{date}.json"
        if ecg_file.exists():
            try:
                with open(ecg_file, 'r') as f:
                    ecg_data = json.load(f)
                    if isinstance(ecg_data, list):
                        ecg_anomalies = ecg_data
            except Exception as e:
                app.logger.error(f"Errore lettura file ECG: {str(e)}")
        
        piezo_file = anomaly_dir / f"piezo_anomalies_{date}.json"
        if piezo_file.exists():
            try:
                with open(piezo_file, 'r') as f:
                    piezo_data = json.load(f)
                    if isinstance(piezo_data, list):
                        piezo_anomalies = piezo_data
            except Exception as e:
                app.logger.error(f"Errore lettura file PIEZO: {str(e)}")
        
        temp_file = anomaly_dir / f"temp_anomalies_{date}.json"
        if temp_file.exists():
            try:
                with open(temp_file, 'r') as f:
                    temp_data = json.load(f)
                    if isinstance(temp_data, list):
                        temp_anomalies = temp_data
            except Exception as e:
                app.logger.error(f"Errore lettura file TEMP: {str(e)}")
        
        ecg_anomalies.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        piezo_anomalies.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        temp_anomalies.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        return jsonify({
            'date': date,
            'ecg_anomalies': ecg_anomalies,
            'piezo_anomalies': piezo_anomalies,
            'temp_anomalies': temp_anomalies,
            'ecg_count': len(ecg_anomalies),
            'piezo_count': len(piezo_anomalies),
            'temp_count': len(temp_anomalies),
            'total_count': len(ecg_anomalies) + len(piezo_anomalies) + len(temp_anomalies)
        })
    
    except Exception as e:
        app.logger.error(f"Errore nel recupero anomalie: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({'error': 'Errore server'}), 500


@require_auth
@app.route('/api/anomalies/summary')
def get_anomalies_summary():
    """Ottieni riepilogo anomalie"""
    try:
        anomaly_dir = Path("anomaly_logs")
        if not anomaly_dir.exists():
            return jsonify({
                'summary': [],
                'total_ecg': 0,
                'total_piezo': 0,
                'total_temp': 0,
                'total': 0
            })
        
        summary = []
        total_ecg = 0
        total_piezo = 0
        total_temp = 0
        
        dates = set()
        for log_file in anomaly_dir.glob("*.json"):
            filename = log_file.stem
            date_str = None
            
            if filename.startswith("anomalies_"):
                date_str = filename.replace("anomalies_", "")
            elif filename.startswith("piezo_anomalies_"):
                date_str = filename.replace("piezo_anomalies_", "")
            elif filename.startswith("temp_anomalies_"):
                date_str = filename.replace("temp_anomalies_", "")
            else:
                continue
            
            if date_str and len(date_str) == 8 and date_str.isdigit():
                dates.add(date_str)
        
        for date in sorted(dates, reverse=True):
            ecg_count = 0
            piezo_count = 0
            temp_count = 0
            
            ecg_file = anomaly_dir / f"anomalies_{date}.json"
            if ecg_file.exists():
                try:
                    with open(ecg_file, 'r') as f:
                        ecg_data = json.load(f)
                        ecg_count = len(ecg_data) if isinstance(ecg_data, list) else 0
                except:
                    pass
            
            piezo_file = anomaly_dir / f"piezo_anomalies_{date}.json"
            if piezo_file.exists():
                try:
                    with open(piezo_file, 'r') as f:
                        piezo_data = json.load(f)
                        piezo_count = len(piezo_data) if isinstance(piezo_data, list) else 0
                except:
                    pass
            
            temp_file = anomaly_dir / f"temp_anomalies_{date}.json"
            if temp_file.exists():
                try:
                    with open(temp_file, 'r') as f:
                        temp_data = json.load(f)
                        temp_count = len(temp_data) if isinstance(temp_data, list) else 0
                except:
                    pass
            
            total_ecg += ecg_count
            total_piezo += piezo_count
            total_temp += temp_count
            
            try:
                dt = datetime.strptime(date, '%Y%m%d')
                date_label = dt.strftime('%d %B %Y')
            except:
                date_label = date
            
            summary.append({
                'date': date,
                'date_label': date_label,
                'ecg_count': ecg_count,
                'piezo_count': piezo_count,
                'temp_count': temp_count,
                'total': ecg_count + piezo_count + temp_count
            })
        
        return jsonify({
            'summary': summary,
            'total_ecg': total_ecg,
            'total_piezo': total_piezo,
            'total_temp': total_temp,
            'total': total_ecg + total_piezo + total_temp
        })
    
    except Exception as e:
        app.logger.error(f"Errore nel recupero summary anomalie: {str(e)}")
        return jsonify({'error': 'Errore server'}), 500


@require_auth
@app.route('/api/anomalies/detail/<date>/<anomaly_type>/<int:index>')
def get_anomaly_detail(date, anomaly_type, index):
    """Ottieni dettaglio di una singola anomalia"""
    if not validate_date_string(date):
        return jsonify({'error': 'Formato data non valido'}), 400
    
    if anomaly_type not in ['ecg', 'piezo', 'temp']:
        return jsonify({'error': 'Tipo anomalia non valido'}), 400
    
    try:
        anomaly_dir = Path("anomaly_logs")
        
        if anomaly_type == 'ecg':
            file_path = anomaly_dir / f"anomalies_{date}.json"
        elif anomaly_type == 'piezo':
            file_path = anomaly_dir / f"piezo_anomalies_{date}.json"
        else:
            file_path = anomaly_dir / f"temp_anomalies_{date}.json"
        
        if not file_path.exists():
            return jsonify({'error': 'File anomalie non trovato'}), 404
        
        with open(file_path, 'r') as f:
            anomalies = json.load(f)
        
        if not isinstance(anomalies, list) or index < 0 or index >= len(anomalies):
            return jsonify({'error': 'Indice anomalia non valido'}), 404
        
        return jsonify({
            'anomaly': anomalies[index],
            'type': anomaly_type,
            'date': date,
            'index': index
        })
    
    except Exception as e:
        app.logger.error(f"Errore nel recupero dettaglio anomalia: {str(e)}")
        return jsonify({'error': 'Errore server'}), 500


# ====== TEST ENDPOINT PER NOTIFICHE ======

@require_auth
@app.route('/api/test/notification/<anomaly_type>', methods=['POST'])
def test_notification(anomaly_type):
    """ENDPOINT DI TEST: Forza invio notifica"""
    if anomaly_type not in ['ecg', 'piezo', 'temp']:
        return jsonify({'error': 'Invalid type'}), 400
    
    # Crea dati fake
    if anomaly_type == 'ecg':
        test_data = {
            'reconstruction_error': 0.5,
            'threshold': 0.1,
            'timestamp': datetime.now().isoformat()
        }
    elif anomaly_type == 'piezo':
        test_data = {
            'reconstruction_error': 0.3,
            'threshold': 0.1,
            'timestamp': datetime.now().isoformat()
        }
    else:  # temp
        test_data = {
            'temperature': 34.5,
            'threshold': 35.0,
            'anomaly_type': 'hypothermia',
            'severity': 'moderate',
            'duration_readings': 5,
            'timestamp': datetime.now().isoformat()
        }
    
    print(f"\n[TEST] Forcing {anomaly_type.upper()} notification")
    send_anomaly_notification(anomaly_type, test_data)
    
    return jsonify({
        'status': 'sent',
        'type': anomaly_type,
        'message': 'Test notification sent'
    })

# ====== SOCKETIO EVENTS ======

@socketio.on('connect', namespace='/data')
def handle_connect():
    """Client connesso"""
    print(f"[Dashboard] Client connesso: {request.sid}")
    emit('connection_response', {'status': 'connected'})

@socketio.on('disconnect', namespace='/data')
def handle_disconnect():
    """Client disconnesso"""
    print(f"[Dashboard] Client disconnesso: {request.sid}")

@socketio.on('request_data', namespace='/data')
def handle_data_request(data):
    """Client richiede dati"""
    signal = data.get('signal', 'ECG')
    if validate_signal_name(signal):
        emit('data_update', {
            'signal': signal,
            'data': prepare_chart_data(signal)
        })

# ====== FUNZIONI STATUS ======

def set_device_status(connected):
    """Aggiorna stato dispositivo"""
    state.device_connected = connected
    socketio.emit('device_status', {'connected': connected}, namespace='/data')

def set_acquisition_status(acquiring):
    """Aggiorna stato acquisizione"""
    state.is_acquiring = acquiring
    if acquiring and state.start_time is None:
        state.start_time = time.time()
    socketio.emit('acquisition_status', {'acquiring': acquiring}, namespace='/data')

def set_current_session(session_id):
    """Imposta sessione corrente"""
    state.current_session_id = session_id

def add_system_log(category, message, level='INFO'):
    """
    Aggiungi log di sistema al buffer
    
    Args:
        category: MQTT, Dashboard, ECG Anomaly, PIEZO Anomaly, TEMP Anomaly, Serial, Config, etc.
        message: Messaggio da loggare
        level: INFO, WARNING, ERROR, DEBUG
    """
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'category': category,
        'level': level,
        'message': message
    }
    state.system_logs.append(log_entry)
    # Emit to connected clients in real-time
    socketio.emit('system_log', log_entry, namespace='/data')


# ====== BACKGROUND THREADS ======

def background_status_updater():
    """Thread per aggiornamenti periodici"""
    while True:
        time.sleep(2)
        if state.device_connected:
            socketio.emit('status_update', {
                'stats': state.stats,
                'packet_count': state.packet_count,
                'uptime': int(time.time() - state.start_time) if state.start_time else 0
            }, namespace='/data')


def background_anomaly_checker():
    """Thread per controllo periodico nuove anomalie"""
    while True:
        time.sleep(3)  # Controlla ogni 3 secondi
        check_for_new_anomalies()


# ====== USB PORTS CONFIGURATION API ======

@require_auth
@app.route('/api/serial-ports/detect', methods=['GET'])
def detect_serial_ports():
    """
    Endpoint per rilevare porte seriali USB disponibili
    """
    try:
        from detect_usb_ports import get_available_ports, load_port_config
        
        # Get available ports
        ports = get_available_ports()
        
        # Get current configuration
        current_config = load_port_config('usb_ports_config.json')
        
        return jsonify({
            'success': True,
            'ports': ports,
            'current_config': current_config
        })
    except Exception as e:
        app.logger.error(f"Error detecting serial ports: {str(e)}")
        return jsonify({'error': str(e)}), 500


@require_auth
@app.route('/api/serial-ports/config', methods=['GET'])
def get_serial_ports_config():
    """
    Endpoint per leggere configurazione porte seriali corrente
    """
    try:
        from detect_usb_ports import load_port_config
        
        config = load_port_config('usb_ports_config.json')
        return jsonify({
            'success': True,
            'config': config
        })
    except Exception as e:
        app.logger.error(f"Error reading serial ports config: {str(e)}")
        return jsonify({'error': str(e)}), 500


@require_auth
@app.route('/api/serial-ports/config', methods=['POST'])
def save_serial_ports_config():
    """
    Endpoint per salvare configurazione porte seriali
    
    Expected JSON body:
    {
        "shell_port": "/dev/tty.usbmodem1201",
        "data_port": "/dev/tty.usbmodem1203"
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'shell_port' not in data or 'data_port' not in data:
            return jsonify({'error': 'Missing shell_port or data_port'}), 400
        
        shell_port = data['shell_port']
        data_port = data['data_port']
        
        if not shell_port or not data_port:
            return jsonify({'error': 'Ports cannot be empty'}), 400
        
        # Save configuration using detect_usb_ports module
        from detect_usb_ports import save_port_config
        
        success = save_port_config(shell_port, data_port, 'usb_ports_config.json')
        
        if not success:
            return jsonify({'error': 'Failed to save configuration'}), 500
        
        app.logger.info(f"USB ports configuration saved: shell={shell_port}, data={data_port}")
        
        return jsonify({
            'success': True,
            'message': 'Configurazione salvata. Riavviare IITdata_acq.py per applicare le modifiche.',
            'config': {
                'shell_port': shell_port,
                'data_port': data_port
            }
        })
        
    except Exception as e:
        app.logger.error(f"Error saving serial ports config: {str(e)}")
        return jsonify({'error': str(e)}), 500


@require_auth
@app.route('/api/serial-ports/restart-acquisition', methods=['POST'])
def restart_acquisition():
    """
    Endpoint per riavviare il processo IITdata_acq.py
    NOTA: Funziona solo se IITdata_acq è stato avviato come subprocess
    """
    try:
        import subprocess
        import signal
        
        # Find and kill existing IITdata_acq process
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'IITdata_acq.py'],
                capture_output=True,
                text=True
            )
            
            if result.stdout:
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    if pid:
                        os.kill(int(pid), signal.SIGTERM)
                        app.logger.info(f"Killed IITdata_acq.py process (PID: {pid})")
                
                # Wait a moment for clean shutdown
                time.sleep(2)
        except Exception as e:
            app.logger.warning(f"Could not kill existing process: {e}")
        
        # Start new process
        subprocess.Popen(
            ['python', 'IITdata_acq.py'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        
        app.logger.info("IITdata_acq.py restarted successfully")
        
        return jsonify({
            'success': True,
            'message': 'IITdata_acq.py riavviato con successo. Attendere la riconnessione...'
        })
        
    except Exception as e:
        app.logger.error(f"Error restarting IITdata_acq: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Impossibile riavviare automaticamente. Errore: {str(e)}',
            'manual_restart': True
        }), 500


# ====== SYSTEM LOGS API ======

@require_auth
@app.route('/api/system/logs', methods=['GET'])
def get_system_logs():
    """
    Recupera i log di sistema con filtri opzionali
    
    Query params:
        - category: MQTT, Dashboard, ECG Anomaly, PIEZO Anomaly, TEMP Anomaly, Serial, Config
        - level: INFO, WARNING, ERROR, DEBUG
        - limit: numero massimo di log da ritornare (default 100)
    """
    try:
        category_filter = request.args.get('category', None)
        level_filter = request.args.get('level', None)
        limit = int(request.args.get('limit', 100))
        
        # Get all logs from deque
        logs = list(state.system_logs)
        
        # Apply filters
        if category_filter:
            logs = [log for log in logs if log['category'] == category_filter]
        
        if level_filter:
            logs = [log for log in logs if log['level'] == level_filter]
        
        # Limit results (most recent first)
        logs = logs[-limit:]
        
        return jsonify({
            'success': True,
            'logs': logs,
            'total': len(logs)
        })
        
    except Exception as e:
        app.logger.error(f"Error getting system logs: {str(e)}")
        return jsonify({'error': str(e)}), 500


@require_auth
@app.route('/api/system/logs/export', methods=['GET'])
def export_system_logs():
    """
    Esporta tutti i log di sistema in formato JSON
    """
    try:
        category_filter = request.args.get('category', None)
        level_filter = request.args.get('level', None)
        
        logs = list(state.system_logs)
        
        if category_filter:
            logs = [log for log in logs if log['category'] == category_filter]
        
        if level_filter:
            logs = [log for log in logs if log['level'] == level_filter]
        
        # Create export data
        export_data = {
            'export_time': datetime.now().isoformat(),
            'total_logs': len(logs),
            'filters': {
                'category': category_filter,
                'level': level_filter
            },
            'logs': logs
        }
        
        return jsonify(export_data)
        
    except Exception as e:
        app.logger.error(f"Error exporting logs: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ====== SIMULATE ANOMALY API ======

@require_auth
@app.route('/api/simulate/anomaly', methods=['POST'])
def simulate_anomaly_endpoint():
    """
    Endpoint per simulare anomalie (ECG, PIEZO, TEMP)
    
    Expected JSON body:
    {
        "type": "ecg" | "piezo" | "temp",
        
        // For ECG/PIEZO:
        "reconstruction_error": float (optional),
        "threshold": float (default 0.1),
        
        // For TEMP:
        "anomaly_type": "hypothermia" | "hyperthermia",
        "temperature": float,
        "threshold": float (default 35.0),
        "duration_readings": int (default 5),
        "severity": "mild" | "moderate" | "severe" (optional, auto-calculated if not provided)
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'type' not in data:
            return jsonify({'error': 'Missing anomaly type'}), 400
        
        anomaly_type = data['type'].lower()
        
        if anomaly_type not in ['ecg', 'piezo', 'temp']:
            return jsonify({'error': 'Invalid anomaly type. Must be ecg, piezo, or temp'}), 400
        
        # Import simulator
        from simulate_anomaly import AnomalySimulator
        simulator = AnomalySimulator(anomaly_logs_dir="anomaly_logs")
        
        # Generate anomaly based on type
        if anomaly_type == 'ecg':
            reconstruction_error = data.get('reconstruction_error')
            threshold = data.get('threshold', 0.1)
            
            anomaly = simulator.simulate_ecg_anomaly(
                reconstruction_error=reconstruction_error,
                threshold=threshold
            )
            
        elif anomaly_type == 'piezo':
            reconstruction_error = data.get('reconstruction_error')
            threshold = data.get('threshold', 0.1)
            
            anomaly = simulator.simulate_piezo_anomaly(
                reconstruction_error=reconstruction_error,
                threshold=threshold
            )
            
        elif anomaly_type == 'temp':
            if 'anomaly_type' not in data or 'temperature' not in data:
                return jsonify({'error': 'Missing required fields for TEMP anomaly'}), 400
            
            temp_type = data['anomaly_type']
            temperature = float(data['temperature'])
            threshold = data.get('threshold', 35.0)
            duration_readings = data.get('duration_readings', 5)
            severity = data.get('severity')  # Can be None
            
            anomaly = simulator.simulate_temp_anomaly(
                anomaly_type=temp_type,
                temperature=temperature,
                threshold=threshold,
                duration_readings=duration_readings,
                severity=severity
            )
        
        # Save anomaly to file
        simulator.save_anomaly(anomaly_type, anomaly)
        
        # Send real-time notification
        send_anomaly_notification(anomaly_type, anomaly)
        
        return jsonify({
            'success': True,
            'message': f'{anomaly_type.upper()} anomaly simulated successfully',
            'anomaly': anomaly
        })
        
    except Exception as e:
        app.logger.error(f"Error simulating anomaly: {str(e)}")
        return jsonify({'error': str(e)}), 500


def run_dashboard(host='0.0.0.0', port=5001, debug=False):
    """Avvia il server dashboard"""
    print(f"[Dashboard] Avvio server su {host}:{port}")
    
    # Crea directory static se non esistono
    os.makedirs('static/js', exist_ok=True)
    os.makedirs('static/css', exist_ok=True)
    
    # Crea directory anomaly_logs se non esiste
    os.makedirs('anomaly_logs', exist_ok=True)
    
    # Avvia thread di background
    status_thread = threading.Thread(target=background_status_updater, daemon=True)
    status_thread.start()
    
    anomaly_thread = threading.Thread(target=background_anomaly_checker, daemon=True)
    anomaly_thread.start()
    
    print("[Dashboard] Background threads avviati (status + anomaly checker)")
    
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


# ========== SESSION CLEANUP THREAD ==========

def cleanup_expired_sessions():
    """Background thread to cleanup expired sessions every 10 minutes"""
    while True:
        time.sleep(600)  # 10 minutes
        try:
            result = auth_db.cleanup_expired_sessions()
            if result['success'] and result['deleted'] > 0:
                print(f"[Auth] Cleaned up {result['deleted']} expired sessions")
        except Exception as e:
            print(f"[Auth] Error cleaning up sessions: {e}")

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_expired_sessions, daemon=True)
cleanup_thread.start()
print("[Auth] Session cleanup thread started")


# ========== MODEL MANAGEMENT API ==========
MODELS_BASE_DIR = Path("models")

@app.route('/api/models/<model_type>/list', methods=['GET'])
@require_auth
def list_models(model_type):
    """Lista tutti i modelli disponibili per un tipo (ecg, piezo)"""
    try:
        model_dir = MODELS_BASE_DIR / model_type
        if not model_dir.exists():
            return jsonify({"success": False, "error": f"Model type '{model_type}' not found"}), 404
        
        active_file = model_dir / "active_model.json"
        active_folder = None
        if active_file.exists():
            with open(active_file, 'r') as f:
                active_data = json.load(f)
                active_folder = active_data.get('model_folder')
        
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
                        "is_active": folder.name == active_folder
                    })
        
        models.sort(key=lambda x: x['version'], reverse=True)
        
        return jsonify({"success": True, "models": models})
    
    except Exception as e:
        print(f"[Models] Error listing: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/models/<model_type>/active', methods=['GET'])
@require_auth
def get_active_model(model_type):
    """Ottieni configurazione del modello attivo"""
    try:
        model_dir = MODELS_BASE_DIR / model_type
        active_file = model_dir / "active_model.json"
        
        if not active_file.exists():
            return jsonify({"success": False, "error": "No active model configured"}), 404
        
        with open(active_file, 'r') as f:
            active_data = json.load(f)
        
        model_folder = active_data.get('model_folder')
        config_file = model_dir / model_folder / "config.json"
        
        config = {}
        if config_file.exists():
            with open(config_file, 'r') as f:
                config = json.load(f)
        
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
        print(f"[Models] Error getting active: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/models/<model_type>/activate', methods=['POST'])
@require_medico_or_admin
def activate_model(model_type):
    """Attiva un modello specifico con una soglia (solo admin)"""
    try:
        data = request.get_json()
        model_folder = data.get('model_folder')
        threshold = data.get('threshold')
        
        if not model_folder or threshold is None:
            return jsonify({"success": False, "error": "Missing model_folder or threshold"}), 400
        
        model_dir = MODELS_BASE_DIR / model_type
        target_model_dir = model_dir / model_folder
        
        if not target_model_dir.exists():
            return jsonify({"success": False, "error": f"Model folder '{model_folder}' not found"}), 404
        
        config_file = target_model_dir / "config.json"
        if not config_file.exists():
            return jsonify({"success": False, "error": "Model config.json not found"}), 404
        
        active_file = model_dir / "active_model.json"
        active_data = {
            "model_folder": model_folder,
            "threshold": float(threshold),
            "selected_date": datetime.now().isoformat()
        }
        
        with open(active_file, 'w') as f:
            json.dump(active_data, f, indent=2)
        
        print(f"[Models] Activated {model_type}/{model_folder} (threshold: {threshold})")
        
        return jsonify({"success": True, "message": f"Model '{model_folder}' activated"})
    
    except Exception as e:
        print(f"[Models] Error activating: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/models/<model_type>/config', methods=['GET'])
@require_auth
def get_model_config(model_type):
    """Ottieni config.json di un modello specifico"""
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
        
        return jsonify({"success": True, "config": config})
    
    except Exception as e:
        print(f"[Models] Error getting config: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/models/<model_type>/<folder>/image/<image_name>', methods=['GET'])
@require_auth
def get_model_image(model_type, folder, image_name):
    """Serve training images from model folder"""
    try:
        # Validate model type
        if model_type not in ['ecg', 'piezo']:
            return jsonify({"success": False, "error": "Invalid model type"}), 400
        
        # Build path to image
        model_dir = MODELS_BASE_DIR / model_type / folder
        image_path = model_dir / image_name
        
        # Security: ensure image is within model directory
        if not str(image_path.resolve()).startswith(str(model_dir.resolve())):
            return jsonify({"success": False, "error": "Invalid image path"}), 403
        
        # Check if image exists
        if not image_path.exists():
            return jsonify({"success": False, "error": "Image not found"}), 404
        
        # Serve the image
        return send_file(image_path, mimetype='image/png')
    
    except Exception as e:
        print(f"[Models] Error serving image: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/models/<model_type>/<folder>/images', methods=['GET'])
@require_auth
def get_model_images_list(model_type, folder):
    """Get list of available images for a model"""
    try:
        model_dir = MODELS_BASE_DIR / model_type / folder
        
        if not model_dir.exists():
            return jsonify({"success": False, "error": "Model folder not found"}), 404
        
        # Common image names from training
        possible_images = [
            'examples.png',
            'training.png', 
            'reconstruction.png',
            'threshold.png'
        ]
        
        available_images = []
        for img in possible_images:
            img_path = model_dir / img
            if img_path.exists():
                available_images.append({
                    'name': img,
                    'url': f'/api/models/{model_type}/{folder}/image/{img}',
                    'title': img.replace('.png', '').replace('_', ' ').title()
                })
        
        return jsonify({
            "success": True, 
            "images": available_images,
            "count": len(available_images)
        })
    
    except Exception as e:
        print(f"[Models] Error listing images: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/models/upload', methods=['POST'])
def upload_model():
    """
    Receive trained model package from training script and extract to models directory
    
    Expected request:
        - Headers: X-API-Key
        - Files: model_package (ZIP file)
        - Data: model_type ('ecg' or 'piezo'), model_name
    
    ZIP structure:
        - model.tflite
        - config.json
        - examples.png
        - training.png
        - reconstruction.png
        - threshold.png
    """
    import zipfile
    import tempfile
    import shutil
    from datetime import datetime
    
    # Check API key
    api_key = request.headers.get('X-API-Key')
    expected_key = os.getenv('MODEL_UPLOAD_API_KEY', 'iit-model-upload-2025')  # Default key
    
    if api_key != expected_key:
        print(f"[Models Upload] Unauthorized attempt - invalid API key")
        return jsonify({"success": False, "error": "Unauthorized - Invalid API key"}), 401
    
    try:
        # Get parameters
        model_type = request.form.get('model_type')
        model_name = request.form.get('model_name')
        zip_file = request.files.get('model_package')
        
        if not model_type or not zip_file:
            return jsonify({"success": False, "error": "Missing model_type or model_package"}), 400
        
        if model_type not in ['ecg', 'piezo']:
            return jsonify({"success": False, "error": "Invalid model_type (must be 'ecg' or 'piezo')"}), 400
        
        print(f"\n[Models Upload] Receiving {model_type.upper()} model...")
        print(f"[Models Upload] Model name: {model_name or 'auto-generated'}")
        
        # Generate model folder name if not provided
        if not model_name:
            timestamp = datetime.now().strftime("%Y%m%d")  # Solo data, no ora
            model_name = f"{model_type}_model_v1_{timestamp}"
        
        # Create model directory
        model_dir = MODELS_BASE_DIR / model_type / model_name
        model_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"[Models Upload] Extracting to: {model_dir}")
        
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp_file:
            zip_file.save(tmp_file.name)
            tmp_path = tmp_file.name
        
        # Extract ZIP
        try:
            with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
                # List contents
                file_list = zip_ref.namelist()
                print(f"[Models Upload] ZIP contains {len(file_list)} files:")
                for filename in file_list:
                    print(f"  - {filename}")
                
                # Extract all files
                zip_ref.extractall(model_dir)
                print(f"[Models Upload] ✓ Files extracted successfully")
                
                # Verify required files
                required_files = ['model.tflite', 'config.json']
                missing_files = [f for f in required_files if not (model_dir / f).exists()]
                
                if missing_files:
                    print(f"[Models Upload] ⚠️  Missing required files: {missing_files}")
                    return jsonify({
                        "success": False, 
                        "error": f"Missing required files: {', '.join(missing_files)}"
                    }), 400
        
        finally:
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        
        print(f"[Models Upload] ✅ Model deployed successfully!")
        print(f"[Models Upload] Location: {model_dir}")
        
        # Return success with model path
        return jsonify({
            "success": True,
            "message": "Model uploaded and extracted successfully",
            "model_path": str(model_dir),
            "model_type": model_type,
            "model_name": model_name,
            "files_extracted": len(file_list)
        })
    
    except zipfile.BadZipFile:
        print(f"[Models Upload] ❌ Invalid ZIP file")
        return jsonify({"success": False, "error": "Invalid ZIP file"}), 400
    
    except Exception as e:
        print(f"[Models Upload] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    run_dashboard(debug=True)