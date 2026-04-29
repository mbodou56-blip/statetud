# ==============================================================
#  StatEtud — Backend Expert
#  Flask + PostgreSQL + WebSockets + Statistiques avancées
#  L2 Informatique — Université de Yaoundé I
# ==============================================================

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import psycopg2
import psycopg2.extras
import statistics
import math
import csv
import io
import os
import time
import html
from datetime import datetime
from collections import Counter
from urllib.parse import urlparse

app = Flask(__name__, static_folder='.')
app.config['SECRET_KEY'] = 'statetud-uy1-secret-2024'
CORS(app, resources={r"/api/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*")

# ── Connexion PostgreSQL ───────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL', '')

def get_db():
    """Retourne une connexion PostgreSQL."""
    url = urlparse(DATABASE_URL)
    conn = psycopg2.connect(
        host=url.hostname,
        port=url.port,
        dbname=url.path[1:],
        user=url.username,
        password=url.password,
        sslmode='require'
    )
    return conn

def init_db():
    """Crée la table si elle n'existe pas."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reponses (
                    id              SERIAL PRIMARY KEY,
                    nom             TEXT    NOT NULL,
                    age             REAL    NOT NULL,
                    niveau          TEXT    NOT NULL,
                    filiere         TEXT    NOT NULL,
                    stress          INTEGER NOT NULL CHECK(stress BETWEEN 1 AND 5),
                    sommeil         REAL    NOT NULL CHECK(sommeil BETWEEN 1 AND 16),
                    repas_par_jour  INTEGER NOT NULL CHECK(repas_par_jour BETWEEN 1 AND 6),
                    heures_etude    REAL    NOT NULL CHECK(heures_etude BETWEEN 0 AND 20),
                    activite_physique TEXT  NOT NULL,
                    moyenne_estimee REAL    NOT NULL CHECK(moyenne_estimee BETWEEN 0 AND 20),
                    timestamp       TEXT    NOT NULL
                )
            """)
        conn.commit()

try:
    init_db()
    print("✅ PostgreSQL connecté et table créée.")
except Exception as e:
    print(f"⚠️ Erreur DB init: {e}")

# ── Rate limiting ──────────────────────────────────────────────
RATE_LIMIT = {}
MAX_REQ = 10
WINDOW  = 60

def is_rate_limited(ip):
    now = time.time()
    timestamps = [t for t in RATE_LIMIT.get(ip, []) if now - t < WINDOW]
    RATE_LIMIT[ip] = timestamps
    if len(timestamps) >= MAX_REQ:
        return True
    RATE_LIMIT[ip].append(now)
    return False

# ── Validation ─────────────────────────────────────────────────
VALID_NIVEAUX  = {'L1','L2','L3','Master 1','Master 2','Doctorat'}
VALID_FILIERES = {'Informatique','Mathématiques','Physique','Chimie',
                  'Biologie','Sciences de la Terre','Autre'}
VALID_ACTIVITE = {'Jamais','Rarement (1x/mois)','Parfois (1x/semaine)',
                  'Régulièrement (3x/semaine)','Quotidiennement'}

def sanitize(value):
    return html.escape(str(value).strip())

def validate_entry(data):
    errors = []
    required = ['nom','age','niveau','filiere','stress','sommeil',
                'repas_par_jour','heures_etude','activite_physique','moyenne_estimee']
    for f in required:
        if f not in data or data[f] is None or str(data[f]).strip() == '':
            errors.append(f"Champ manquant : {f}")
    if errors:
        return errors
    try:
        age = float(data['age'])
        if not (15 <= age <= 50): errors.append("Âge invalide (15-50)")
    except: errors.append("Âge invalide")
    try:
        s = int(data['stress'])
        if not (1 <= s <= 5): errors.append("Stress entre 1 et 5")
    except: errors.append("Stress invalide")
    try:
        s = float(data['sommeil'])
        if not (1 <= s <= 16): errors.append("Sommeil invalide")
    except: errors.append("Sommeil invalide")
    try:
        r = int(data['repas_par_jour'])
        if not (1 <= r <= 6): errors.append("Repas invalide")
    except: errors.append("Repas invalide")
    try:
        e = float(data['heures_etude'])
        if not (0 <= e <= 20): errors.append("Heures étude invalides")
    except: errors.append("Heures étude invalides")
    try:
        m = float(data['moyenne_estimee'])
        if not (0 <= m <= 20): errors.append("Moyenne invalide")
    except: errors.append("Moyenne invalide")
    if data.get('niveau') not in VALID_NIVEAUX: errors.append("Niveau invalide")
    if data.get('filiere') not in VALID_FILIERES: errors.append("Filière invalide")
    if data.get('activite_physique') not in VALID_ACTIVITE: errors.append("Activité invalide")
    nom = str(data.get('nom', '')).strip()
    if len(nom) < 2 or len(nom) > 40: errors.append("Prénom : 2-40 caractères")
    return errors

# ── Statistiques descriptives ──────────────────────────────────
def compute_stats(values):
    if not values:
        return {}
    n    = len(values)
    mean = round(statistics.mean(values), 3)
    med  = round(statistics.median(values), 3)
    mode_v = Counter(values).most_common(1)[0][0]
    std  = round(statistics.stdev(values), 3) if n > 1 else 0.0
    var  = round(statistics.variance(values), 3) if n > 1 else 0.0
    mn, mx = min(values), max(values)
    rng  = round(mx - mn, 3)
    q1   = round(statistics.quantiles(values, n=4)[0], 3) if n >= 4 else mn
    q3   = round(statistics.quantiles(values, n=4)[2], 3) if n >= 4 else mx
    iqr  = round(q3 - q1, 3)
    cv   = round((std / mean) * 100, 2) if mean != 0 else 0.0
    skew = 0.0
    if std > 0 and n >= 3:
        skew = round(sum((v - mean)**3 for v in values) / (n * std**3), 3)
    return dict(n=n, mean=mean, median=med, mode=mode_v, std=std,
                variance=var, min=mn, max=mx, range=rng,
                q1=q1, q3=q3, iqr=iqr, cv=cv, skewness=skew,
                whisker_low=round(max(mn, q1-1.5*iqr), 3),
                whisker_high=round(min(mx, q3+1.5*iqr), 3))

def pearson(x, y):
    n = len(x)
    if n < 3: return None
    mx, my = statistics.mean(x), statistics.mean(y)
    num = sum((a-mx)*(b-my) for a,b in zip(x,y))
    den = math.sqrt(sum((a-mx)**2 for a in x) * sum((b-my)**2 for b in y))
    return round(num/den, 4) if den != 0 else 0.0

def t_test_one_sample(values, mu0=10.0):
    n = len(values)
    if n < 3: return None
    mean = statistics.mean(values)
    std  = statistics.stdev(values)
    t    = round((mean - mu0) / (std / math.sqrt(n)), 4)
    return {'t_stat': t, 'n': n, 'mean': round(mean,3), 'std': round(std,3),
            'mu0': mu0, 'interpretation': 'Significatif (|t|>2)' if abs(t)>2 else 'Non significatif'}

def linear_regression(x, y):
    n = len(x)
    if n < 3: return None
    mx, my = statistics.mean(x), statistics.mean(y)
    sxy = sum((a-mx)*(b-my) for a,b in zip(x,y))
    sxx = sum((a-mx)**2 for a in x)
    if sxx == 0: return None
    b1 = round(sxy/sxx, 4)
    b0 = round(my - b1*mx, 4)
    y_hat = [b0 + b1*xi for xi in x]
    ss_res = sum((yi-yhi)**2 for yi,yhi in zip(y,y_hat))
    ss_tot = sum((yi-my)**2 for yi in y)
    r2 = round(1 - ss_res/ss_tot, 4) if ss_tot != 0 else 0.0
    r  = round(math.sqrt(abs(r2)) * (1 if b1>=0 else -1), 4)
    return {'b0': b0, 'b1': b1, 'r2': r2, 'r': r,
            'x_vals': [round(v,2) for v in x],
            'y_vals': [round(v,2) for v in y],
            'y_pred': [round(v,2) for v in y_hat],
            'interpretation': f"y = {b0} + {b1}*x   R²={r2}"}

def chi2_test(cat_a, cat_b):
    rows = sorted(set(cat_a)); cols = sorted(set(cat_b))
    if len(rows) < 2 or len(cols) < 2: return None
    n = len(cat_a)
    table = {r: {c: 0 for c in cols} for r in rows}
    for a,b in zip(cat_a, cat_b):
        table[a][b] += 1
    chi2 = 0.0
    for r in rows:
        for c in cols:
            obs = table[r][c]
            rs  = sum(table[r].values())
            cs  = sum(table[rr][c] for rr in rows)
            exp = (rs*cs)/n
            if exp > 0: chi2 += (obs-exp)**2/exp
    ddl = (len(rows)-1)*(len(cols)-1)
    return {'chi2': round(chi2,4), 'ddl': ddl,
            'interpretation': 'Dépendance probable (χ²>3.84)' if chi2>3.84 else 'Indépendance probable'}

# ── Helper : convertir une Row psycopg2 en dict ────────────────
def row_to_dict(row, cursor):
    cols = [desc[0] for desc in cursor.description]
    return dict(zip(cols, row))

# ═══════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/submit', methods=['POST'])
def submit():
    ip = request.remote_addr
    if is_rate_limited(ip):
        return jsonify({'error': 'Trop de soumissions. Attendez 1 minute.'}), 429
    raw = request.get_json(silent=True)
    if not raw:
        return jsonify({'error': 'Payload JSON invalide'}), 400
    errors = validate_entry(raw)
    if errors:
        return jsonify({'error': ' | '.join(errors)}), 422
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO reponses
                    (nom,age,niveau,filiere,stress,sommeil,repas_par_jour,
                     heures_etude,activite_physique,moyenne_estimee,timestamp)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (sanitize(raw['nom']), float(raw['age']), raw['niveau'],
                      raw['filiere'], int(raw['stress']), float(raw['sommeil']),
                      int(raw['repas_par_jour']), float(raw['heures_etude']),
                      raw['activite_physique'], float(raw['moyenne_estimee']),
                      datetime.now().isoformat()))
                cur.execute("SELECT COUNT(*) FROM reponses")
                total = cur.fetchone()[0]
            conn.commit()
        socketio.emit('new_response', {'total': total})
        return jsonify({'success': True, 'message': 'Données enregistrées !', 'total': total})
    except Exception as e:
        return jsonify({'error': f'Erreur DB: {str(e)}'}), 500

@app.route('/api/data', methods=['GET'])
def get_data():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reponses ORDER BY id DESC")
            rows = cur.fetchall()
            data = [row_to_dict(r, cur) for r in rows]
    return jsonify(data)

@app.route('/api/stats', methods=['GET'])
def get_stats():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reponses")
            rows = cur.fetchall()
            data = [row_to_dict(r, cur) for r in rows]
    if not data:
        return jsonify({'error': 'Aucune donnée'}), 404

    def col(k): return [d[k] for d in data if d.get(k) is not None]

    age=col('age'); stress=col('stress'); sommeil=col('sommeil')
    repas=col('repas_par_jour'); etude=col('heures_etude'); moy=col('moyenne_estimee')

    vars_num = {'stress':stress,'sommeil':sommeil,'heures_etude':etude,
                'repas_par_jour':repas,'moyenne_estimee':moy,'age':age}
    correlations = {}
    keys = list(vars_num.keys())
    for i in range(len(keys)):
        for j in range(i+1, len(keys)):
            k1,k2 = keys[i],keys[j]
            v1,v2 = vars_num[k1],vars_num[k2]
            if len(v1)==len(v2) and len(v1)>=3:
                correlations[f"{k1}|{k2}"] = pearson(v1,v2)

    return jsonify({
        'total': len(data),
        'numeriques': {
            'age': compute_stats(age), 'stress': compute_stats(stress),
            'sommeil': compute_stats(sommeil), 'repas_par_jour': compute_stats(repas),
            'heures_etude': compute_stats(etude), 'moyenne_estimee': compute_stats(moy)
        },
        'categoriques': {
            'niveaux': dict(Counter(col('niveau'))),
            'filieres': dict(Counter(col('filiere'))),
            'activite_physique': dict(Counter(col('activite_physique')))
        },
        'distributions': {
            'stress': {str(k):v for k,v in sorted(Counter([int(s) for s in stress]).items())},
            'sommeil': sommeil, 'moyenne': moy
        },
        'correlations': correlations,
        'regressions': {
            'etude_vs_moyenne':   linear_regression(etude, moy),
            'stress_vs_moyenne':  linear_regression(stress, moy),
            'sommeil_vs_moyenne': linear_regression(sommeil, moy)
        },
        'tests': {
            't_test_moyenne': t_test_one_sample(moy, 10.0),
            'chi2_stress_filiere': chi2_test([str(d['stress']) for d in data], col('filiere'))
        },
        'boxplots': {k: compute_stats(v) for k,v in vars_num.items() if v}
    })

@app.route('/api/export/csv')
def export_csv():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reponses ORDER BY id")
            rows = cur.fetchall()
            data = [row_to_dict(r, cur) for r in rows]
    if not data:
        return jsonify({'error': 'Aucune donnée'}), 404
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID','Prénom','Âge','Niveau','Filière','Stress','Sommeil(h)',
                     'Repas/j','Étude(h)','Activité','Moyenne/20','Date'])
    for r in data:
        writer.writerow([r['id'],r['nom'],r['age'],r['niveau'],r['filiere'],
                         r['stress'],r['sommeil'],r['repas_par_jour'],
                         r['heures_etude'],r['activite_physique'],r['moyenne_estimee'],r['timestamp']])
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':'attachment; filename=statetud_donnees.csv'})

@app.route('/api/export/pdf')
def export_pdf():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reponses ORDER BY id")
            rows = cur.fetchall()
            data = [row_to_dict(r, cur) for r in rows]
    if not data:
        return jsonify({'error': 'Aucune donnée'}), 404

    moy=[d['moyenne_estimee'] for d in data]; stress=[d['stress'] for d in data]
    sommeil=[d['sommeil'] for d in data]; etude=[d['heures_etude'] for d in data]

    def stat_row(label, vals):
        if not vals: return ''
        s = compute_stats(vals)
        return f"<tr><td>{label}</td><td>{s['n']}</td><td>{s['mean']}</td><td>{s['median']}</td><td>{s['std']}</td><td>{s['min']}</td><td>{s['max']}</td><td>{s['cv']}%</td></tr>"

    data_rows = ''.join(
        f"<tr><td>{r['id']}</td><td>{r['nom']}</td><td>{r['age']}</td><td>{r['niveau']}</td>"
        f"<td>{r['filiere']}</td><td>{r['stress']}/5</td><td>{r['sommeil']}h</td>"
        f"<td>{r['heures_etude']}h</td><td>{r['moyenne_estimee']}/20</td></tr>"
        for r in data)

    html_out = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<title>Rapport StatEtud — Mbodou Mahamat 21T2615</title>
<style>
body{{font-family:Arial,sans-serif;margin:40px;color:#1E293B;font-size:13px}}
h1{{color:#1E3A5F;font-size:20px}} h2{{color:#3A6EBF;font-size:14px;border-bottom:2px solid #3A6EBF;padding-bottom:4px;margin-top:24px}}
.meta{{color:#64748B;font-size:11px;margin-bottom:20px}}
.kpi{{display:flex;gap:12px;margin:12px 0}}
.kpi div{{background:#EFF6FF;border-left:4px solid #3A6EBF;padding:8px 14px;border-radius:4px;flex:1}}
.kpi .val{{font-size:20px;font-weight:bold;color:#1E3A5F}} .kpi .lbl{{font-size:10px;color:#64748B}}
table{{width:100%;border-collapse:collapse;font-size:11px;margin-top:8px}}
th{{background:#1E3A5F;color:white;padding:6px 8px;text-align:left}}
td{{padding:5px 8px;border-bottom:1px solid #E2E8F0}}
@media print{{button{{display:none}}}}
</style></head><body>
<h1>Rapport d'Analyse — StatEtud</h1>
<div class="meta">
  Université de Yaoundé I · L2 Informatique · {datetime.now().strftime('%d/%m/%Y à %H:%M')}<br>
  Étudiant : <strong>Mbodou Mahamat</strong> · Matricule : <strong>21T2615</strong>
</div>
<div class="kpi">
  <div><div class="val">{len(data)}</div><div class="lbl">Répondants</div></div>
  <div><div class="val">{round(statistics.mean(moy),2) if moy else '-'}/20</div><div class="lbl">Moyenne</div></div>
  <div><div class="val">{round(statistics.mean(stress),2) if stress else '-'}/5</div><div class="lbl">Stress</div></div>
  <div><div class="val">{round(statistics.mean(sommeil),2) if sommeil else '-'}h</div><div class="lbl">Sommeil</div></div>
</div>
<h2>Statistiques Descriptives</h2>
<table><thead><tr><th>Variable</th><th>N</th><th>Moyenne</th><th>Médiane</th><th>Écart-type</th><th>Min</th><th>Max</th><th>CV%</th></tr></thead>
<tbody>{stat_row('Moyenne /20',moy)}{stat_row('Stress (1-5)',stress)}{stat_row('Sommeil (h)',sommeil)}{stat_row("Heures étude",etude)}</tbody></table>
<h2>Données Collectées ({len(data)} réponses)</h2>
<table><thead><tr><th>#</th><th>Prénom</th><th>Âge</th><th>Niveau</th><th>Filière</th><th>Stress</th><th>Sommeil</th><th>Étude</th><th>Moyenne</th></tr></thead>
<tbody>{data_rows}</tbody></table>
<br>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:16px">
  <button onclick="window.print()" style="background:#1E3A5F;color:white;padding:10px 24px;border:none;border-radius:6px;cursor:pointer;font-size:13px;">🖨️ Imprimer / Sauvegarder PDF</button>
  <button onclick="window.close()" style="background:white;color:#1E3A5F;padding:10px 24px;border:2px solid #1E3A5F;border-radius:6px;cursor:pointer;font-size:13px;">✖ Fermer cet onglet</button>
  <a href="/" style="display:inline-flex;align-items:center;padding:10px 24px;background:white;color:#3A6EBF;border:2px solid #3A6EBF;border-radius:6px;font-size:13px;text-decoration:none;">⬅ Retour à l'application</a>
</div>
</body></html>"""
    return Response(html_out, mimetype='text/html')

@app.route('/api/reset', methods=['DELETE'])
def reset():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reponses")
            cur.execute("ALTER SEQUENCE reponses_id_seq RESTART WITH 1")
        conn.commit()
    socketio.emit('new_response', {'total': 0})
    return jsonify({'success': True, 'message': 'Données réinitialisées.'})

@socketio.on('connect')
def on_connect():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM reponses")
            total = cur.fetchone()[0]
    emit('new_response', {'total': total})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    print(f"🟢 StatEtud Expert — http://localhost:{port}")
    socketio.run(app, debug=debug, port=port, host='0.0.0.0', allow_unsafe_werkzeug=True)
