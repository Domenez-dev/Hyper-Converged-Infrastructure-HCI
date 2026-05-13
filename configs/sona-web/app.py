import os
import time
import threading
import psycopg2
from datetime import datetime
from flask import Flask, request, g, jsonify, render_template_string

app = Flask(__name__)

DB_CONFIG = {
    'host': '192.168.10.115',
    'database': 'sonadb',
    'user': 'sona',
    'password': 'sona123'
}

INSTANCE_ID = os.environ.get('INSTANCE_ID', 'app-1')
HOSTNAME = os.environ.get('HOSTNAME', 'app-1')

def log_request_async(path, client_ip, user_agent):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO requests (instance_id, path, client_ip, user_agent)
            VALUES (%s, %s, %s, %s)
        """, (INSTANCE_ID, path, client_ip, user_agent))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Erreur DB: {e}")

@app.before_request
def before():
    g.start = time.time()

@app.after_request
def after(response):
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    user_agent = request.headers.get('User-Agent', 'unknown')
    
    if request.path != '/health' and request.path != '/api/db-stats' and request.path != '/api/last-requests':
        threading.Thread(
            target=log_request_async,
            args=(request.path, client_ip, user_agent),
            daemon=True
        ).start()
    return response

@app.route('/health')
def health():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.close()
        return 'ok', 200
    except:
        return 'db_error', 500

@app.route('/')
def index():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/status')
def status():
    db_ok = False
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.close()
        db_ok = True
    except:
        db_ok = False
    
    return jsonify({
        'instance_id': INSTANCE_ID,
        'hostname': HOSTNAME,
        'db_ok': db_ok,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/api/last-requests')
def last_requests():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, instance_id, path, client_ip, request_time 
            FROM requests 
            ORDER BY id DESC 
            LIMIT 20
        """)
        requests_data = [{'id': row[0], 'instance': row[1], 'path': row[2], 'client_ip': row[3], 'time': str(row[4])} for row in cur.fetchall()]
        cur.close()
        conn.close()
        return jsonify({'requests': requests_data})
    except Exception as e:
        return jsonify({'error': str(e), 'requests': []})

@app.route('/api/db-stats')
def db_stats():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) FROM requests")
        total_requests = cur.fetchone()[0]
        
        cur.execute("""
            SELECT instance_id, COUNT(*) 
            FROM requests 
            GROUP BY instance_id
        """)
        per_instance = [{'instance': row[0], 'count': row[1]} for row in cur.fetchall()]
        
        cur.execute("SELECT pg_database_size('sonadb') / 1024 / 1024 as size_mb")
        db_size = cur.fetchone()[0]
        
        cur.execute("""
            SELECT COUNT(*) FROM requests 
            WHERE request_time > now() - interval '1 hour'
        """)
        last_hour = cur.fetchone()[0]
        
        cur.close()
        conn.close()
        
        return jsonify({
            'total_requests': total_requests,
            'per_instance': per_instance,
            'db_size_mb': db_size or 0,
            'last_hour': last_hour
        })
    except Exception as e:
        return jsonify({'error': str(e)})

DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sona-Web - HA Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            padding: 20px;
            min-height: 100vh;
        }
        
        .container { max-width: 1400px; margin: 0 auto; }
        
        .header {
            background: white;
            border-radius: 20px;
            padding: 25px;
            margin-bottom: 25px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        
        .header h1 {
            font-size: 28px;
            color: #2d3748;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        
        .status-card {
            background: #f7fafc;
            padding: 15px;
            border-radius: 15px;
            text-align: center;
        }
        
        .status-card .label {
            font-size: 12px;
            color: #718096;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }
        
        .status-card .value {
            font-size: 24px;
            font-weight: bold;
            color: #2d3748;
        }
        
        .status-card .value.ok { color: #48bb78; }
        .status-card .value.error { color: #f56565; }
        
        .card {
            background: white;
            border-radius: 20px;
            padding: 25px;
            margin-bottom: 25px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        
        .card h2 {
            font-size: 18px;
            color: #2d3748;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .instances-row {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        
        .instance-card {
            flex: 1;
            background: #f7fafc;
            border-radius: 15px;
            padding: 20px;
            text-align: center;
            min-width: 200px;
        }
        
        .instance-card.app-1 { border-left: 5px solid #48bb78; }
        .instance-card.app-2 { border-left: 5px solid #4299e1; }
        
        .instance-title {
            font-size: 20px;
            font-weight: bold;
            margin-bottom: 10px;
        }
        
        .instance-title.app-1 { color: #22543d; }
        .instance-title.app-2 { color: #2c5282; }
        
        .instance-count {
            font-size: 36px;
            font-weight: bold;
            color: #2d3748;
        }
        
        .instance-percent {
            font-size: 14px;
            color: #718096;
            margin-top: 5px;
        }
        
        .table-responsive { overflow-x: auto; }
        
        table { width: 100%; border-collapse: collapse; }
        
        th {
            text-align: left;
            padding: 12px;
            background: #f7fafc;
            color: #4a5568;
            font-size: 12px;
            font-weight: 600;
        }
        
        td { padding: 10px 12px; border-bottom: 1px solid #e2e8f0; font-size: 13px; }
        
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }
        
        .badge.app-1 { background: #c6f6d5; color: #22543d; }
        .badge.app-2 { background: #bee3f8; color: #2c5282; }
        
        .path { font-family: monospace; font-size: 11px; color: #718096; }
        
        .refresh-time {
            text-align: right;
            font-size: 12px;
            color: #a0aec0;
            margin-top: 15px;
        }
        
        .live-dot {
            display: inline-block;
            width: 10px;
            height: 10px;
            background: #48bb78;
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.5; transform: scale(0.8); }
        }
        
        .empty-state { text-align: center; padding: 30px; color: #a0aec0; }
        
        @keyframes highlight {
            0% { background-color: #c6f6d5; }
            100% { background-color: transparent; }
        }
        
        .new-row {
            animation: highlight 1s ease-out;
        }
        
        .refresh-1s {
            font-size: 11px;
            color: #48bb78;
            margin-left: 10px;
        }
        
        .bar-container {
            margin-top: 15px;
        }
        
        .bar-wrapper {
            display: flex;
            height: 20px;
            border-radius: 10px;
            overflow: hidden;
        }
        
        .bar-app1 {
            background: #48bb78;
            height: 100%;
            transition: width 0.5s ease;
        }
        
        .bar-app2 {
            background: #4299e1;
            height: 100%;
            transition: width 0.5s ease;
        }
        
        .bar-labels {
            display: flex;
            justify-content: space-between;
            margin-top: 8px;
            font-size: 12px;
        }
        
        .label-app1 { color: #22543d; }
        .label-app2 { color: #2c5282; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>
                <span class="live-dot"></span>
                📊 Sona-Web - High Availability Dashboard
            </h1>
            <div class="status-grid">
                <div class="status-card">
                    <div class="label">Instance Active</div>
                    <div class="value" id="instanceId">-</div>
                </div>
                <div class="status-card">
                    <div class="label">PostgreSQL</div>
                    <div class="value" id="dbStatus">-</div>
                </div>
                <div class="status-card">
                    <div class="label">Total Requêtes</div>
                    <div class="value" id="totalRequests">0</div>
                </div>
                <div class="status-card">
                    <div class="label">Dernière Heure</div>
                    <div class="value" id="lastHour">0</div>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>📊 Distribution par Instance <span class="refresh-1s">(auto-refresh 1s)</span></h2>
            <div class="instances-row" id="instancesRow">
                <div class="instance-card app-1">
                    <div class="instance-title app-1">app-1</div>
                    <div class="instance-count" id="countApp1">0</div>
                    <div class="instance-percent" id="percentApp1">0%</div>
                </div>
                <div class="instance-card app-2">
                    <div class="instance-title app-2">app-2</div>
                    <div class="instance-count" id="countApp2">0</div>
                    <div class="instance-percent" id="percentApp2">0%</div>
                </div>
            </div>
            <div class="bar-container">
                <div class="bar-wrapper">
                    <div class="bar-app1" id="barApp1" style="width: 50%;"></div>
                    <div class="bar-app2" id="barApp2" style="width: 50%;"></div>
                </div>
                <div class="bar-labels">
                    <span class="label-app1">app-1</span>
                    <span class="label-app2">app-2</span>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>📝 Dernières Requêtes (temps réel)</h2>
            <div id="lastRequests">Chargement...</div>
        </div>

        <div class="refresh-time">
            <span>🔄 Auto-refresh toutes les 1s</span>
            <span style="margin-left: 15px;">Dernière mise à jour: <span id="lastRefresh">-</span></span>
        </div>
    </div>

    <script>
        let previousRequestIds = new Set();
        
        function formatTime(isoString) {
            if (!isoString) return '-';
            const date = new Date(isoString);
            return date.toLocaleTimeString('fr-FR');
        }
        
        async function refreshData() {
            try {
                const statusRes = await fetch('/api/status');
                const status = await statusRes.json();
                
                document.getElementById('instanceId').innerHTML = status.instance_id || '-';
                const dbElem = document.getElementById('dbStatus');
                if (status.db_ok) {
                    dbElem.innerHTML = '<span class="ok">✅ Connecté</span>';
                } else {
                    dbElem.innerHTML = '<span class="error">❌ Hors ligne</span>';
                }
                
                const statsRes = await fetch('/api/db-stats');
                const stats = await statsRes.json();
                
                if (stats.error) {
                    console.error('Stats error:', stats.error);
                    return;
                }
                
                document.getElementById('totalRequests').innerHTML = (stats.total_requests || 0).toLocaleString();
                document.getElementById('lastHour').innerHTML = (stats.last_hour || 0).toLocaleString();
                document.getElementById('lastRefresh').innerHTML = new Date().toLocaleTimeString('fr-FR');
                
                const perInstance = stats.per_instance || [];
                const total = stats.total_requests || 1;
                
                let count1 = 0, count2 = 0;
                for (let inst of perInstance) {
                    if (inst.instance === 'app-1') count1 = inst.count;
                    if (inst.instance === 'app-2') count2 = inst.count;
                }
                
                const percent1 = total > 0 ? Math.round((count1 / total) * 100) : 0;
                const percent2 = total > 0 ? Math.round((count2 / total) * 100) : 0;
                
                document.getElementById('countApp1').innerHTML = count1.toLocaleString();
                document.getElementById('countApp2').innerHTML = count2.toLocaleString();
                document.getElementById('percentApp1').innerHTML = `${percent1}%`;
                document.getElementById('percentApp2').innerHTML = `${percent2}%`;
                document.getElementById('barApp1').style.width = `${percent1}%`;
                document.getElementById('barApp2').style.width = `${percent2}%`;
                
                const reqRes = await fetch('/api/last-requests');
                const reqData = await reqRes.json();
                
                if (reqData.requests && reqData.requests.length > 0) {
                    let tableHtml = `
                        <div class="table-responsive">
                            <table>
                                <thead>
                                    <tr><th>ID</th><th>Instance</th><th>Chemin</th><th>IP Client</th><th>Date</th></tr>
                                </thead>
                                <tbody>
                    `;
                    for (let req of reqData.requests) {
                        const isNew = !previousRequestIds.has(req.id) && previousRequestIds.size > 0;
                        tableHtml += `
                            <tr class="${isNew ? 'new-row' : ''}">
                                <td>${req.id}</td>
                                <td><span class="badge ${req.instance}">${req.instance}</span></td>
                                <td class="path">${req.path}</td>
                                <td>${req.client_ip || '-'}</td>
                                <td>${formatTime(req.time)}</td>
                            </tr>
                        `;
                    }
                    tableHtml += '</tbody></table></div>';
                    document.getElementById('lastRequests').innerHTML = tableHtml;
                    
                    const currentIds = new Set();
                    for (let req of reqData.requests) {
                        currentIds.add(req.id);
                    }
                    previousRequestIds = currentIds;
                } else {
                    document.getElementById('lastRequests').innerHTML = '<div class="empty-state">⚠️ Aucune requête enregistrée.</div>';
                }
                
            } catch (err) {
                console.error('Refresh error:', err);
            }
        }
        
        refreshData();
        setInterval(refreshData, 1000);
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
