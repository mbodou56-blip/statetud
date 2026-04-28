# ==============================================================
#  StatEtud — Backend Expert
#  Flask + SQLite + WebSockets + Statistiques avancées
#  L2 Informatique — Université de Yaoundé I
# ==============================================================

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import sqlite3
import statistics
import math
import csv
import io
import os
import time
import html
from datetime import datetime
from collections import Counter

app = Flask(__name__, static_folder='.')
app.config['SECRET_KEY'] = 'statetud-uy1-secret-2024'
CORS(app, resources={r"/api/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*")

DB_FILE = 'statetud.db'

# ── Rate limiting simple (en mémoire) ─────────────────────────
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

# ── Base de données SQLite ─────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reponses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
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

init_db()

# ── Validation serveur stricte ─────────────────────────────────
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
    except: errors.append("Âge doit être un nombre")

    try:
        s = int(data['stress'])
        if not (1 <= s <= 5): errors.append("Stress entre 1 et 5")
    except: errors.append("Stress invalide")

    try:
        s = float(data['sommeil'])
        if not (1 <= s <= 16): errors.append("Sommeil invalide (1-16h)")
    except: errors.append("Sommeil invalide")

    try:
        r = int(data['repas_par_jour'])
        if not (1 <= r <= 6): errors.append("Repas invalide (1-6)")
    except: errors.append("Repas invalide")

    try:
        e = float(data['heures_etude'])
        if not (0 <= e <= 20): errors.append("Heures étude invalides (0-20)")
    except: errors.append("Heures étude invalides")

    try:
        m = float(data['moyenne_estimee'])
        if not (0 <= m <= 20): errors.append("Moyenne invalide (0-20)")
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

# ── Corrélation de Pearson ─────────────────────────────────────
def pearson(x, y):
    n = len(x)
    if n < 3: return None
    mx, my = statistics.mean(x), statistics.mean(y)
    num = sum((a-mx)*(b-my) for a,b in zip(x,y))
    den = math.sqrt(sum((a-mx)**2 for a in x) * sum((b-my)**2 for b in y))
    return round(num/den, 4) if den != 0 else 0.0

# ── Test t de Student ──────────────────────────────────────────
def t_test_one_sample(values, mu0=10.0):
    n = len(values)
    if n < 3: return None
    mean = statistics.mean(values)
    std  = statistics.stdev(values)
    t    = round((mean - mu0) / (std / math.sqrt(n)), 4)
    return {'t_stat': t, 'n': n, 'mean': round(mean,3), 'std': round(std,3),
            'mu0': mu0, 'interpretation': 'Significatif (|t|>2)' if abs(t)>2 else 'Non significatif'}

# ── Régression linéaire (OLS) ──────────────────────────────────
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

# ── Test Chi² ─────────────────────────────────────────────────
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
            conn.execute("""
                INSERT INTO reponses
                (nom,age,niveau,filiere,stress,sommeil,repas_par_jour,
                 heures_etude,activite_physique,moyenne_estimee,timestamp)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (sanitize(raw['nom']), float(raw['age']), raw['niveau'],
                  raw['filiere'], int(raw['stress']), float(raw['sommeil']),
                  int(raw['repas_par_jour']), float(raw['heures_etude']),
                  raw['activite_physique'], float(raw['moyenne_estimee']),
                  datetime.now().isoformat()))
            conn.commit()
            total = conn.execute("SELECT COUNT(*) FROM reponses").fetchone()[0]
        socketio.emit('new_response', {'total': total})
        return jsonify({'success': True, 'message': 'Données enregistrées !', 'total': total})
    except Exception as e:
        return jsonify({'error': f'Erreur DB: {str(e)}'}), 500

@app.route('/api/data', methods=['GET'])
def get_data():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM reponses ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/stats', methods=['GET'])
def get_stats():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM reponses").fetchall()
    if not rows:
        return jsonify({'error': 'Aucune donnée'}), 404
    data = [dict(r) for r in rows]
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
        rows = conn.execute("SELECT * FROM reponses ORDER BY id").fetchall()
    if not rows:
        return jsonify({'error': 'Aucune donnée'}), 404
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID','Prénom','Âge','Niveau','Filière','Stress','Sommeil(h)',
                     'Repas/j','Étude(h)','Activité','Moyenne/20','Date'])
    for r in rows:
        writer.writerow([r['id'],r['nom'],r['age'],r['niveau'],r['filiere'],
                         r['stress'],r['sommeil'],r['repas_par_jour'],
                         r['heures_etude'],r['activite_physique'],r['moyenne_estimee'],r['timestamp']])
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':'attachment; filename=statetud_donnees.csv'})

@app.route('/api/export/pdf')
def export_pdf():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM reponses ORDER BY id").fetchall()
    if not rows:
        return jsonify({'error': 'Aucune donnée'}), 404

    data   = [dict(r) for r in rows]
    n_tot  = len(data)
    moy    = [d['moyenne_estimee'] for d in data]
    stress = [d['stress']          for d in data]
    somm   = [d['sommeil']         for d in data]
    etude  = [d['heures_etude']    for d in data]
    repas  = [d['repas_par_jour']  for d in data]
    age    = [d['age']             for d in data]

    sm  = compute_stats(moy)
    ss  = compute_stats(stress)
    sso = compute_stats(somm)
    se  = compute_stats(etude)
    sr  = compute_stats(repas)
    sa  = compute_stats(age)

    from collections import Counter
    niveaux  = dict(Counter(d['niveau']  for d in data))
    filieres = dict(Counter(d['filiere'] for d in data))
    activite = dict(Counter(d['activite_physique'] for d in data))

    # ── Corrélations clés ────────────────────────────────────────
    r_etude_moy  = pearson(etude, moy)  if len(etude)==len(moy)  else None
    r_stress_moy = pearson(stress, moy) if len(stress)==len(moy) else None
    r_somm_moy   = pearson(somm,  moy)  if len(somm)==len(moy)   else None
    reg_em = linear_regression(etude, moy)
    tt     = t_test_one_sample(moy, 10.0)
    chi2   = chi2_test([str(d['stress']) for d in data], [d['filiere'] for d in data])

    # ── Narration automatique ────────────────────────────────────
    def narrate_sommeil(mean_s):
        if mean_s < 6:
            return (f"La moyenne de sommeil est de <strong>{mean_s}h</strong> par nuit, "
                    f"en dessous du seuil recommandé de 7h. Cela suggère une dette de sommeil "
                    f"préoccupante au sein de la population étudiante étudiée.")
        elif mean_s < 7:
            return (f"La moyenne de sommeil est de <strong>{mean_s}h</strong> par nuit, "
                    f"légèrement en dessous des 7h recommandées. Des efforts de sensibilisation "
                    f"sur l'hygiène du sommeil pourraient être bénéfiques.")
        else:
            return (f"La moyenne de sommeil est de <strong>{mean_s}h</strong> par nuit, "
                    f"conforme aux recommandations de santé. La population étudiée maintient "
                    f"un rythme de sommeil satisfaisant.")

    def narrate_stress(mean_s):
        if mean_s >= 4:
            return (f"Le niveau de stress moyen est de <strong>{mean_s}/5</strong>, ce qui est "
                    f"<strong>élevé</strong>. Une intervention de soutien psychologique ou "
                    f"d'accompagnement académique est fortement recommandée.")
        elif mean_s >= 3:
            return (f"Le niveau de stress moyen est de <strong>{mean_s}/5</strong>, niveau "
                    f"<strong>modéré</strong>. Les étudiants ressentent une pression académique "
                    f"notable sans être en situation de détresse.")
        else:
            return (f"Le niveau de stress moyen est de <strong>{mean_s}/5</strong>, "
                    f"relativement <strong>faible</strong>. Les conditions d'apprentissage "
                    f"semblent globalement sereines.")

    def narrate_moy(mean_m, t_stat):
        appreciation = "insuffisante" if mean_m < 10 else ("passable" if mean_m < 12 else ("assez bonne" if mean_m < 14 else "bonne"))
        sig = f"Le test t (t={t_stat}) confirme que cet écart est statistiquement significatif." if t_stat and abs(t_stat)>2 else "Le test t ne détecte pas d'écart significatif avec la référence de 10/20."
        return (f"La moyenne académique estimée est de <strong>{mean_m}/20</strong>, "
                f"jugée <strong>{appreciation}</strong>. {sig}")

    def narrate_corr(r, x_label, y_label):
        if r is None: return ""
        a = abs(r)
        dir_ = "positive" if r > 0 else "négative"
        force = "forte" if a>=0.7 else ("modérée" if a>=0.4 else "faible")
        sens = ("plus les étudiants étudient, meilleure est leur moyenne" if r>0
                else "plus les étudiants sont stressés, plus leur moyenne tend à baisser")
        return (f"La corrélation entre <em>{x_label}</em> et <em>{y_label}</em> est "
                f"<strong>{force} {dir_}</strong> (r = {r}). En d'autres termes : {sens}.")

    def narrate_reg(reg):
        if not reg: return "Données insuffisantes pour établir une régression."
        qual = "explique bien" if reg['r2']>=0.5 else ("explique partiellement" if reg['r2']>=0.25 else "explique peu")
        return (f"Le modèle <strong>y = {reg['b0']} + {reg['b1']}×x</strong> "
                f"{qual} la variabilité de la moyenne (R² = {reg['r2']}). "
                f"Chaque heure d'étude supplémentaire est associée à une variation de "
                f"<strong>{reg['b1']} points</strong> sur la moyenne.")

    # ── Données JSON pour les graphiques JS ──────────────────────
    import json as _json
    stress_freq  = dict(Counter(int(s) for s in stress))
    niveau_json  = _json.dumps(niveaux)
    filiere_json = _json.dumps(filieres)
    activite_json= _json.dumps(activite)
    stress_json  = _json.dumps({str(i): stress_freq.get(i,0) for i in range(1,6)})
    bins         = [sum(1 for v in somm if v<5), sum(1 for v in somm if 5<=v<6),
                    sum(1 for v in somm if 6<=v<7), sum(1 for v in somm if 7<=v<8),
                    sum(1 for v in somm if v>=8)]
    somm_json    = _json.dumps(bins)
    scatter_json = _json.dumps([{"x": round(etude[i],2), "y": round(moy[i],2)} for i in range(len(etude))])
    reg_json     = _json.dumps([{"x": round(reg_em['x_vals'][i],2), "y": round(reg_em['y_pred'][i],2)} for i in range(len(reg_em['x_vals']))] if reg_em else [])

    def stat_row(label, s):
        if not s: return ''
        return (f"<tr><td><strong>{label}</strong></td><td>{s['n']}</td>"
                f"<td>{s['mean']}</td><td>{s['median']}</td><td>{s['mode']}</td>"
                f"<td>{s['std']}</td><td>{s['variance']}</td>"
                f"<td>{s['min']}</td><td>{s['max']}</td>"
                f"<td>{s['q1']}</td><td>{s['q3']}</td><td>{s['iqr']}</td>"
                f"<td>{s['cv']}%</td><td>{s['skewness']}</td></tr>")

    data_rows = ''.join(
        f"<tr><td>{r['id']}</td><td>{r['nom']}</td><td>{r['age']}</td>"
        f"<td>{r['niveau']}</td><td>{r['filiere']}</td><td>{r['stress']}/5</td>"
        f"<td>{r['sommeil']}h</td><td>{r['heures_etude']}h</td>"
        f"<td>{r['moyenne_estimee']}/20</td></tr>"
        for r in rows)

    date_str = datetime.now().strftime('%d %B %Y à %H:%M')
    pal = "['#1E3A5F','#2A5298','#3A6EBF','#5A8FD6','#7CA9E0','#94A3B8','#CBD5E1']"

    html_out = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Rapport Scientifique — StatEtud</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=Nunito:wght@400;600;700;800&display=swap');
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Nunito',Arial,sans-serif;background:#F8FAFC;color:#1E293B;font-size:13px}}
  .page{{max-width:900px;margin:0 auto;padding:0 0 60px}}

  /* ── Couverture ── */
  .cover{{background:linear-gradient(135deg,#1E3A5F,#3A6EBF);color:white;padding:56px 48px;margin-bottom:0;position:relative;overflow:hidden}}
  .cover-deco{{position:absolute;top:-60px;right:-60px;width:300px;height:300px;border-radius:50%;background:rgba(124,169,224,0.15)}}
  .cover h1{{font-family:'Playfair Display',serif;font-size:2.2rem;font-weight:900;line-height:1.2;position:relative;z-index:2}}
  .cover h1 span{{color:#7CA9E0}}
  .cover .subtitle{{color:rgba(255,255,255,0.7);margin-top:10px;font-size:0.95rem;position:relative;z-index:2}}
  .cover .meta-badges{{display:flex;gap:10px;flex-wrap:wrap;margin-top:24px;position:relative;z-index:2}}
  .cover .mbadge{{background:rgba(255,255,255,0.12);border:1px solid rgba(255,255,255,0.2);padding:6px 14px;border-radius:100px;font-size:0.78rem;font-weight:700}}

  /* ── Barre couleur ── */
  .colorbar{{height:5px;background:linear-gradient(to right,#1E3A5F,#3A6EBF,#7CA9E0,#3A6EBF,#1E3A5F)}}

  /* ── Contenu ── */
  .content{{padding:40px 48px}}
  .section-title{{font-family:'Playfair Display',serif;font-size:1.3rem;font-weight:700;color:#1E3A5F;border-bottom:2px solid #3A6EBF;padding-bottom:6px;margin:36px 0 16px}}
  .section-title span{{color:#3A6EBF}}

  /* ── Résumé exécutif ── */
  .exec-summary{{background:#EFF6FF;border-left:5px solid #3A6EBF;border-radius:0 10px 10px 0;padding:20px 24px;margin-bottom:28px}}
  .exec-summary h3{{font-family:'Playfair Display',serif;color:#1E3A5F;font-size:1rem;margin-bottom:10px}}
  .exec-summary p{{line-height:1.75;color:#334155;font-size:0.88rem;margin-bottom:8px}}
  .exec-summary p:last-child{{margin-bottom:0}}

  /* ── KPI ── */
  .kpi-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:28px}}
  .kpi{{background:white;border-radius:10px;padding:18px 14px;box-shadow:0 2px 10px rgba(30,41,59,0.07);border-top:4px solid #3A6EBF;text-align:center}}
  .kpi:nth-child(2){{border-top-color:#5A8FD6}}
  .kpi:nth-child(3){{border-top-color:#7CA9E0}}
  .kpi:nth-child(4){{border-top-color:#2A5298}}
  .kpi .val{{font-family:'Playfair Display',serif;font-size:1.9rem;font-weight:900;color:#1E3A5F;line-height:1}}
  .kpi .lbl{{font-size:0.72rem;font-weight:800;color:#64748B;text-transform:uppercase;letter-spacing:.4px;margin-top:4px}}

  /* ── Narration ── */
  .narr{{background:white;border-radius:10px;padding:18px 22px;margin-bottom:14px;box-shadow:0 2px 10px rgba(30,41,59,0.06);border-left:4px solid #7CA9E0;font-size:0.87rem;line-height:1.75;color:#334155}}
  .narr .narr-title{{font-weight:800;color:#1E3A5F;font-size:0.78rem;text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px}}

  /* ── Tableaux ── */
  table{{width:100%;border-collapse:collapse;font-size:11px;margin-top:8px;margin-bottom:20px}}
  th{{background:#1E3A5F;color:white;padding:8px 10px;text-align:left;font-size:0.7rem;text-transform:uppercase;letter-spacing:.3px}}
  th:first-child{{border-radius:6px 0 0 0}} th:last-child{{border-radius:0 6px 0 0}}
  td{{padding:7px 10px;border-bottom:1px solid #EEF2F7;color:#334155}}
  tr:hover td{{background:#F8FAFC}}
  tr:last-child td{{border-bottom:none}}

  /* ── Graphiques ── */
  .charts-grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}}
  .chart-box{{background:white;border-radius:10px;padding:20px;box-shadow:0 2px 10px rgba(30,41,59,0.07)}}
  .chart-box.full{{grid-column:1/-1}}
  .chart-box h4{{font-family:'Playfair Display',serif;font-size:0.95rem;color:#1E3A5F;margin-bottom:12px}}
  .chart-box canvas{{max-height:220px}}

  /* ── Tests stat ── */
  .test-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}}
  .test-box{{background:white;border-radius:10px;padding:18px 20px;box-shadow:0 2px 10px rgba(30,41,59,0.07)}}
  .test-box h4{{font-family:'Playfair Display',serif;font-size:0.95rem;color:#1E3A5F;margin-bottom:10px}}
  .test-row{{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #EEF2F7;font-size:0.82rem}}
  .test-row:last-of-type{{border-bottom:none}}
  .tk{{color:#64748B}} .tv{{font-weight:800;color:#1E3A5F}}
  .verdict{{margin-top:10px;padding:7px 12px;border-radius:6px;font-size:0.8rem;font-weight:700}}
  .v-sig{{background:#ECFDF5;color:#065F46}} .v-ns{{background:#FEF9C3;color:#713F12}}

  /* ── Footer ── */
  .report-footer{{margin-top:40px;padding-top:16px;border-top:1px solid #E2E8F0;display:flex;justify-content:space-between;align-items:center;font-size:0.75rem;color:#94A3B8}}

  /* ── Boutons ── */
  .btn-bar{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:32px}}
  .btn{{padding:11px 22px;border-radius:7px;cursor:pointer;font-family:'Nunito',sans-serif;font-size:0.88rem;font-weight:800;border:none;transition:all .2s}}
  .btn-primary{{background:#1E3A5F;color:white}}
  .btn-primary:hover{{background:#2A5298}}
  .btn-outline{{background:white;color:#1E3A5F;border:2px solid #1E3A5F}}
  .btn-outline:hover{{background:#EFF6FF}}
  .btn-blue{{background:white;color:#3A6EBF;border:2px solid #3A6EBF}}
  .btn-blue:hover{{background:#EFF6FF}}
  a.btn{{text-decoration:none;display:inline-flex;align-items:center}}
  @media print{{.btn-bar{{display:none}} body{{background:white}} .chart-box,.test-box,.kpi,.narr,.exec-summary{{box-shadow:none;border:1px solid #E2E8F0}}}}
</style>
</head>
<body>
<div class="page">

<!-- ── Couverture ── -->
<div class="cover">
  <div class="cover-deco"></div>
  <h1>Rapport <span>Scientifique</span><br>d'Analyse Descriptive</h1>
  <p class="subtitle">Étude sur la santé et le bien-être des étudiants</p>
  <div class="meta-badges">
    <span class="mbadge">📍 Université de Yaoundé I</span>
    <span class="mbadge">🎓 L2 Informatique</span>
    <span class="mbadge">📅 {date_str}</span>
    <span class="mbadge">👥 {n_tot} répondant(s)</span>
    <span class="mbadge">🔬 StatEtud v2.0</span>
  </div>
</div>
<div class="colorbar"></div>

<div class="content">

  <!-- ── Boutons ── -->
  <div class="btn-bar" style="margin-top:28px">
    <button class="btn btn-primary" onclick="window.print()">🖨️ Imprimer / Sauvegarder en PDF</button>
    <button class="btn btn-outline" onclick="window.close()">✖ Fermer cet onglet</button>
    <a class="btn btn-blue" href="/">⬅ Retour à l'application</a>
  </div>

  <!-- ── Résumé exécutif ── -->
  <div class="section-title">1. <span>Résumé Exécutif</span></div>
  <div class="exec-summary">
    <h3>Synthèse de l'étude — {n_tot} étudiant(s) interrogé(s)</h3>
    <p>{narrate_sommeil(sso.get('mean','–'))}</p>
    <p>{narrate_stress(ss.get('mean','–'))}</p>
    <p>{narrate_moy(sm.get('mean','–'), tt['t_stat'] if tt else None)}</p>
    <p>La corrélation entre les heures d'étude et la performance académique est
    <strong>{'positive (r=' + str(r_etude_moy) + ')' if r_etude_moy and r_etude_moy > 0 else 'négative ou nulle'}</strong>,
    tandis que le stress présente une corrélation
    <strong>{'négative (r=' + str(r_stress_moy) + ') — le stress nuit aux résultats' if r_stress_moy and r_stress_moy < 0 else 'faible avec la moyenne'}</strong>.</p>
  </div>

  <!-- ── KPI ── -->
  <div class="kpi-row">
    <div class="kpi"><div class="val">{n_tot}</div><div class="lbl">Répondants</div></div>
    <div class="kpi"><div class="val">{sm.get('mean','–')}<small style="font-size:.85rem">/20</small></div><div class="lbl">Moyenne moy.</div></div>
    <div class="kpi"><div class="val">{ss.get('mean','–')}<small style="font-size:.85rem">/5</small></div><div class="lbl">Stress moyen</div></div>
    <div class="kpi"><div class="val">{sso.get('mean','–')}<small style="font-size:.85rem">h</small></div><div class="lbl">Sommeil moyen</div></div>
  </div>

  <!-- ── Stats descriptives ── -->
  <div class="section-title">2. <span>Statistiques Descriptives</span></div>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>Variable</th><th>N</th><th>Moyenne</th><th>Médiane</th><th>Mode</th>
      <th>σ</th><th>σ²</th><th>Min</th><th>Max</th>
      <th>Q1</th><th>Q3</th><th>IQR</th><th>CV%</th><th>Asymétrie</th>
    </tr></thead>
    <tbody>
      {stat_row('Moyenne /20', sm)}
      {stat_row('Stress (1-5)', ss)}
      {stat_row('Sommeil (h)', sso)}
      {stat_row("Heures d'étude", se)}
      {stat_row('Repas/jour', sr)}
      {stat_row('Âge (ans)', sa)}
    </tbody>
  </table>
  </div>

  <!-- ── Interprétations ── -->
  <div class="section-title">3. <span>Interprétations Automatiques</span></div>
  <div class="narr"><div class="narr-title">Sommeil</div>{narrate_sommeil(sso.get('mean','–'))}</div>
  <div class="narr"><div class="narr-title">Stress académique</div>{narrate_stress(ss.get('mean','–'))}</div>
  <div class="narr"><div class="narr-title">Performance académique</div>{narrate_moy(sm.get('mean','–'), tt['t_stat'] if tt else None)}</div>
  <div class="narr"><div class="narr-title">Corrélation Étude → Moyenne</div>{narrate_corr(r_etude_moy, "heures d'étude", "moyenne")}</div>
  <div class="narr"><div class="narr-title">Corrélation Stress → Moyenne</div>{narrate_corr(r_stress_moy, "niveau de stress", "moyenne")}</div>
  <div class="narr"><div class="narr-title">Régression linéaire (Étude → Moyenne)</div>{narrate_reg(reg_em)}</div>

  <!-- ── Graphiques ── -->
  <div class="section-title">4. <span>Visualisations</span></div>
  <div class="charts-grid">
    <div class="chart-box"><h4>Distribution du Stress</h4><canvas id="c1"></canvas></div>
    <div class="chart-box"><h4>Répartition par Niveau</h4><canvas id="c2"></canvas></div>
    <div class="chart-box"><h4>Distribution du Sommeil</h4><canvas id="c3"></canvas></div>
    <div class="chart-box"><h4>Activité Physique</h4><canvas id="c4"></canvas></div>
    <div class="chart-box full"><h4>Régression Linéaire — Heures d'étude vs Moyenne</h4><canvas id="c5"></canvas></div>
    <div class="chart-box full"><h4>Étudiants par Filière</h4><canvas id="c6"></canvas></div>
  </div>

  <!-- ── Tests statistiques ── -->
  <div class="section-title">5. <span>Tests Statistiques</span></div>
  <div class="test-grid">
    <div class="test-box">
      <h4>Test t de Student (μ₀ = 10/20)</h4>
      {''.join([
        f'<div class="test-row"><span class="tk">Moyenne observée</span><span class="tv">{tt["mean"]}/20</span></div>',
        f'<div class="test-row"><span class="tk">Écart-type</span><span class="tv">{tt["std"]}</span></div>',
        f'<div class="test-row"><span class="tk">t statistique</span><span class="tv">{tt["t_stat"]}</span></div>',
        f'<div class="test-row"><span class="tk">N</span><span class="tv">{tt["n"]}</span></div>',
        f'<div class="verdict {"v-sig" if abs(tt["t_stat"])>2 else "v-ns"}">{tt["interpretation"]}</div>'
      ]) if tt else '<p style="color:#94A3B8;font-size:.82rem">Données insuffisantes.</p>'}
    </div>
    <div class="test-box">
      <h4>Test Chi² — Stress vs Filière</h4>
      {''.join([
        f'<div class="test-row"><span class="tk">χ² calculé</span><span class="tv">{chi2["chi2"]}</span></div>',
        f'<div class="test-row"><span class="tk">Degrés de liberté</span><span class="tv">{chi2["ddl"]}</span></div>',
        f'<div class="test-row"><span class="tk">Seuil critique (α=5%)</span><span class="tv">3.84</span></div>',
        f'<div class="verdict {"v-sig" if chi2["chi2"]>3.84 else "v-ns"}">{chi2["interpretation"]}</div>'
      ]) if chi2 else '<p style="color:#94A3B8;font-size:.82rem">Données insuffisantes.</p>'}
    </div>
    <div class="test-box">
      <h4>Corrélations de Pearson</h4>
      <div class="test-row"><span class="tk">Étude ↔ Moyenne</span><span class="tv">{r_etude_moy if r_etude_moy is not None else 'N/D'}</span></div>
      <div class="test-row"><span class="tk">Stress ↔ Moyenne</span><span class="tv">{r_stress_moy if r_stress_moy is not None else 'N/D'}</span></div>
      <div class="test-row"><span class="tk">Sommeil ↔ Moyenne</span><span class="tv">{r_somm_moy if r_somm_moy is not None else 'N/D'}</span></div>
    </div>
    <div class="test-box">
      <h4>Régression OLS — Étude → Moyenne</h4>
      {''.join([
        f'<div class="test-row"><span class="tk">Équation</span><span class="tv">y = {reg_em["b0"]} + {reg_em["b1"]}×x</span></div>',
        f'<div class="test-row"><span class="tk">R² (déterm.)</span><span class="tv">{reg_em["r2"]}</span></div>',
        f'<div class="test-row"><span class="tk">r (Pearson)</span><span class="tv">{reg_em["r"]}</span></div>',
        f'<div class="verdict {"v-sig" if reg_em["r2"]>=0.3 else "v-ns"}">{"R² significatif" if reg_em["r2"]>=0.3 else "R² faible"}</div>'
      ]) if reg_em else '<p style="color:#94A3B8;font-size:.82rem">Données insuffisantes.</p>'}
    </div>
  </div>

  <!-- ── Données brutes ── -->
  <div class="section-title">6. <span>Données Collectées</span></div>
  <div style="overflow-x:auto">
  <table>
    <thead><tr><th>#</th><th>Prénom</th><th>Âge</th><th>Niveau</th><th>Filière</th>
    <th>Stress</th><th>Sommeil</th><th>Étude</th><th>Moyenne</th></tr></thead>
    <tbody>{data_rows}</tbody>
  </table>
  </div>

  <!-- ── Footer ── -->
  <div class="report-footer">
    <span>StatEtud v2.0 — Université de Yaoundé I · L2 Informatique</span>
    <span>Généré le {date_str}</span>
  </div>

  <!-- ── Boutons bas ── -->
  <div class="btn-bar" style="margin-top:28px">
    <button class="btn btn-primary" onclick="window.print()">🖨️ Imprimer / Sauvegarder en PDF</button>
    <button class="btn btn-outline" onclick="window.close()">✖ Fermer cet onglet</button>
    <a class="btn btn-blue" href="/">⬅ Retour à l'application</a>
  </div>

</div><!-- /content -->
</div><!-- /page -->

<script>
const pal = {pal};
const stressD = {stress_json};
const niveauD = {niveau_json};
const filiereD= {filiere_json};
const activD  = {activite_json};
const sommD   = {somm_json};
const scatterD= {scatter_json};
const regD    = {reg_json};

new Chart(document.getElementById('c1'),{{
  type:'bar', data:{{labels:['1 😌','2','3 😐','4','5 😰'],
  datasets:[{{label:'Étudiants',data:[1,2,3,4,5].map(i=>stressD[String(i)]||0),
    backgroundColor:pal,borderRadius:6}}]}},
  options:{{responsive:true,plugins:{{legend:{{display:false}}}}}}
}});

const nlabs=Object.keys(niveauD);
new Chart(document.getElementById('c2'),{{
  type:'doughnut', data:{{labels:nlabs,datasets:[{{data:Object.values(niveauD),
    backgroundColor:pal.slice(0,nlabs.length),borderWidth:2,borderColor:'#fff'}}]}},
  options:{{responsive:true,plugins:{{legend:{{position:'right'}}}}}}
}});

new Chart(document.getElementById('c3'),{{
  type:'bar', data:{{labels:['<5h','5-6h','6-7h','7-8h','8h+'],
  datasets:[{{label:'Étudiants',data:sommD,backgroundColor:'#3A6EBF',borderRadius:6}}]}},
  options:{{responsive:true,plugins:{{legend:{{display:false}}}}}}
}});

const alabs=Object.keys(activD);
new Chart(document.getElementById('c4'),{{
  type:'pie', data:{{labels:alabs,datasets:[{{data:Object.values(activD),
    backgroundColor:pal.slice(0,alabs.length),borderColor:'#fff',borderWidth:2}}]}},
  options:{{responsive:true,plugins:{{legend:{{position:'bottom',labels:{{boxWidth:10}}}}}}}}
}});

new Chart(document.getElementById('c5'),{{
  type:'scatter',
  data:{{datasets:[
    {{label:'Observations',data:scatterD,backgroundColor:'rgba(58,110,191,0.55)',pointRadius:6}},
    {{label:'Droite de régression',data:regD,type:'line',borderColor:'#1E3A5F',
      borderWidth:2,pointRadius:0,fill:false}}
  ]}},
  options:{{responsive:true,plugins:{{legend:{{position:'bottom'}}}},
    scales:{{x:{{title:{{display:true,text:"Heures d'étude"}}}},
             y:{{title:{{display:true,text:'Moyenne /20'}}}}}}}}
}});

const flabs=Object.keys(filiereD);
new Chart(document.getElementById('c6'),{{
  type:'bar', data:{{labels:flabs,datasets:[{{label:'Étudiants',
    data:Object.values(filiereD),backgroundColor:'rgba(58,110,191,0.75)',borderRadius:6}}]}},
  options:{{responsive:true,plugins:{{legend:{{display:false}}}},
    scales:{{y:{{beginAtZero:true,ticks:{{stepSize:1}}}}}}}}
}});
</script>
</body>
</html>"""
    return Response(html_out, mimetype='text/html')

@app.route('/api/reset', methods=['DELETE'])
def reset():
    with get_db() as conn:
        conn.execute("DELETE FROM reponses")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='reponses'")
        conn.commit()
    socketio.emit('new_response', {'total': 0})
    return jsonify({'success': True, 'message': 'Données réinitialisées.'})

@socketio.on('connect')
def on_connect():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM reponses").fetchone()[0]
    emit('new_response', {'total': total})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    print(f"🟢 StatEtud Expert — http://localhost:{port}")
    socketio.run(app, debug=debug, port=port, host='0.0.0.0', allow_unsafe_werkzeug=True)
