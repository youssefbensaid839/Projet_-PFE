# app.py - Projet PFE - Plateforme décisionnelle CDR sortants multi-opérateurs

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import JWTManager, create_access_token, create_refresh_token, jwt_required, get_jwt_identity, set_access_cookies, set_refresh_cookies, unset_jwt_cookies
from datetime import datetime, timedelta
from sqlalchemy.orm import joinedload
import pyodbc
import secrets
import os
from flask_mail import Mail, Message

# Brevo pour emails futurs
import sib_api_v3_sdk
from sib_api_v3_sdk import Configuration, ApiClient, TransactionalEmailsApi, SendSmtpEmail
from sib_api_v3_sdk.rest import ApiException
from urllib.parse import quote  # à ajouter en haut de votre fichier si pas déjà présent
 
# =====================================================================
# CONSTANTE : URL de base Power BI (identique pour tous les dashboards)
# =====================================================================
POWERBI_BASE = (
    "https://app.powerbi.com/reportEmbed?reportId=49c058b3-614b-48ec-a925-aa7b067a00a8&autoAuth=true&ctid=604f1a96-cbe8-43f8-abbf-f8eaf5d85730"
)
 
 
def build_powerbi_url(do_id, do_nom, campagnes):
    """
    Construit l'URL Power BI avec filtres sur le DO et les campagnes associées.
 
    Power BI URL filtering syntax :
      - Filtre simple  : &filter=Table/Colonne eq 'valeur'
      - Filtre multiple (OR) : &filter=Table/Colonne eq 'v1' or Table/Colonne eq 'v2'
 
    ⚠️  Les noms de tables/colonnes doivent correspondre EXACTEMENT
        à ce qui est dans votre dataset Power BI.
        Ajustez 'DonneurdOrdre', 'Campagne', 'nom' si nécessaire.
    """
 
    filtres = []
 
    # --- Filtre 1 : restreindre au DO de l'utilisateur (par nom, plus fiable que l'id) ---
    do_nom_encode = do_nom.replace("'", "''")   # échapper les apostrophes
    filtres.append(f"DonneurdOrdre/nom eq '{do_nom_encode}'")
 
    # --- Filtre 2 : restreindre aux campagnes de ce DO ---
    if campagnes:
        campagne_conditions = " or ".join(
            f"Campagne/nom eq '{c.nom.replace(chr(39), chr(39)*2)}'"
            for c in campagnes
        )
        filtres.append(f"({campagne_conditions})")
 
    # Combiner les filtres avec AND
    filtre_complet = " and ".join(filtres)
 
    # Power BI attend le filtre encodé dans l'URL
    url = POWERBI_BASE + "&filter=" + quote(filtre_complet, safe="()/'= ")
 
    return url


configuration = sib_api_v3_sdk.Configuration()
configuration.api_key['api-key'] = os.getenv('BREVO_API_KEY', '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')

api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))

app = Flask(__name__)
app.secret_key = 'super-secret-key-9876543210abcdef'

# Configuration JWT
app.config['JWT_SECRET_KEY'] = 'ta-clé-secrète-très-longue-et-unique-ici-2026'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(minutes=30)
app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=7)
app.config['JWT_TOKEN_LOCATION'] = ['cookies']
app.config['JWT_COOKIE_SECURE'] = False
app.config['JWT_COOKIE_SAMESITE'] = 'Lax'
app.config['JWT_COOKIE_CSRF_PROTECT'] = False
# ── Config Flask-Mail (ajoutez dans votre app config) ─────────────────
app.config['MAIL_SERVER']   = 'smtp.gmail.com'
app.config['MAIL_PORT']     = 587
app.config['MAIL_USE_TLS']  = True
app.config['MAIL_USERNAME'] = 'youssefbensaid839@gmail.com'
app.config['MAIL_PASSWORD'] = 'iqhe pemt gorm umfe'
app.config['MAIL_DEFAULT_SENDER'] = ('Outsourcia Facturation', 'youssefbensaid839@gmail.com')
mail = Mail(app)

jwt = JWTManager(app)
# Ajoute ceci après la création de jwt = JWTManager(app)

@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    return render_template('token_expired.html'), 401

# Base de données
app.config['SQLALCHEMY_DATABASE_URI'] = (
    'mssql+pyodbc://@'
    '(localdb)\\MSSQLLocalDB'
    '/TestCRUD'
    '?driver=ODBC+Driver+18+for+SQL+Server'
    '&Trusted_Connection=yes'
    '&TrustServerCertificate=yes'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ═══════════════════════════════════════════════════════════════
# TABLE D'ASSOCIATION MANY-TO-MANY
# ═══════════════════════════════════════════════════════════════
utilisateur_donneurs = db.Table('utilisateur_donneurs',
    db.Column('utilisateur_id', db.Integer, db.ForeignKey('utilisateurs.id'), primary_key=True),
    db.Column('donneur_id', db.Integer, db.ForeignKey('DonneurdOrdre.id'), primary_key=True)  # ✅ Utilise DonneurdOrdre (pas donneurs_ordre)
)
class FacturePlanifiee(db.Model):
    __tablename__ = 'facture_planifiee'
    id             = db.Column(db.Integer, primary_key=True)
    do_id          = db.Column(db.Integer, db.ForeignKey('DonneurdOrdre.id'), nullable=False)
    do             = db.relationship('DonneurdOrdre')
    date_debut     = db.Column(db.DateTime, nullable=False)
    date_fin       = db.Column(db.DateTime, nullable=False)
    montant_total  = db.Column(db.Float, default=0.0)
    nb_campagnes   = db.Column(db.Integer, default=0)
    nb_appels      = db.Column(db.Integer, default=0)
    generee_le     = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Planification envoi
    email_destinataire = db.Column(db.String(255))
    liste_diffusion    = db.Column(db.Text)
    date_envoi_planif  = db.Column(db.DateTime)
    envoyee            = db.Column(db.Boolean, default=False)
    envoyee_le         = db.Column(db.DateTime)
    numero_facture     = db.Column(db.String(50))
    
    # ✅ NOUVEAUX CHAMPS pour la récurrence
    recurrence_active  = db.Column(db.Boolean, default=False)      # Envoi récurrent activé ?
    periode_type       = db.Column(db.String(30))                  # 'journaliere', 'hebdomadaire', 'mensuelle', 'trimestrielle'
    prochain_envoi     = db.Column(db.DateTime)                    # Date du prochain envoi automatique
    derniere_generation = db.Column(db.DateTime)                   # Dernière fois qu'une facture a été générée
class CDR(db.Model):
    __tablename__ = "CDR"

    id = db.Column(db.Integer, primary_key=True)

    Campagne = db.Column(db.String(255))

    # colonne nettoyée (FLOAT)
    cout_appel_float = db.Column(db.Float)

    # colonne date propre
    date_heure_dt = db.Column(db.DateTime)

class Utilisateur(db.Model):
    __tablename__ = 'utilisateurs'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100), nullable=False)
    numtel = db.Column(db.String(20), nullable=False)
    adresse = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    mot_de_passe = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False)  # 'admin', 'responsable_plateau', 'manager'
    donneurs = db.relationship('DonneurdOrdre', 
                                secondary=utilisateur_donneurs,
                                lazy='dynamic',
                                backref=db.backref('utilisateurs', lazy='dynamic'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
class DonneurdOrdre(db.Model):
    __tablename__ = 'DonneurdOrdre'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    adresse = db.Column(db.String(255))
    telephone = db.Column(db.String(20))
    email = db.Column(db.String(120))

class Campagne(db.Model):
    __tablename__ = 'Campagne'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    date_debut = db.Column(db.DateTime)
    date_fin = db.Column(db.DateTime)
    do_id = db.Column(db.Integer, db.ForeignKey('DonneurdOrdre.id'), nullable=False)
    do = db.relationship('DonneurdOrdre', backref='campagnes', lazy=True)
    
    # ✅ Change le backref pour éviter le conflit
    npvs = db.relationship('NPV', backref='campagne_parent', lazy=True)


class NPV(db.Model):
    __tablename__ = 'NPV'
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(50), unique=True, nullable=False)
    statut = db.Column(db.String(50))
    campagne_id = db.Column(db.Integer, db.ForeignKey('Campagne.id'), nullable=True)
    do_id = db.Column(db.Integer, db.ForeignKey('DonneurdOrdre.id'), nullable=True)
    operateur_id = db.Column(db.Integer, db.ForeignKey('Operateur.id'), nullable=True)  # Une seule fois !
    
    campagne = db.relationship('Campagne', backref='npvs_list', lazy=True)
    do = db.relationship('DonneurdOrdre', backref='npvs', lazy=True)
    operateur = db.relationship('Operateur', backref='npvs', lazy=True)

class Operateur(db.Model):
    __tablename__ = 'Operateur'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20))
    pays = db.Column(db.String(100), nullable=False)  
from datetime import datetime

class Historique(db.Model):
    __tablename__ = 'Historique'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('utilisateurs.id'), nullable=False)
    
    action = db.Column(db.String(50), nullable=False)
    entite = db.Column(db.String(50), nullable=False)
    entite_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.String(500), nullable=True)
    date_action = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('Utilisateur', backref='historiques', lazy=True)

    def __repr__(self):
        return f"<Historique {self.action} {self.entite}>"
        

# Création tables + admin par défaut
with app.app_context():
    db.create_all()

    admin_email = 'youssefbensaid839@gmail.com'
    admin = Utilisateur.query.filter_by(email=admin_email).first()
    if not admin:
        hashed = generate_password_hash('123456')
        admin = Utilisateur(
            nom='Admin',
            prenom='Youssef',
            numtel='00000000',
            adresse='Tunis',
            email=admin_email,
            mot_de_passe=hashed,
            role='admin'
        )
        db.session.add(admin)
        db.session.commit()
        print(f"Compte admin créé : {admin_email} / 123456")
    else:
        print("Compte admin existe déjà.")
    

# ------------------------------
# Routes
# ------------------------------

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = Utilisateur.query.filter_by(email=email).first()
        if user and check_password_hash(user.mot_de_passe, password):
            identity = str(user.id)
            access_token = create_access_token(identity=identity)
            refresh_token = create_refresh_token(identity=identity)

            # Redirection selon le rôle
            if user.role == 'admin':
                resp = redirect(url_for('admin_dashboard'))
            elif user.role == 'responsable_plateau':
                resp = redirect(url_for('responsable_dashboard'))
            elif user.role == 'manager':
                resp = redirect(url_for('manager_dashboard'))
            else:
                flash('Rôle non reconnu', 'error')
                return redirect(url_for('login'))

            set_access_cookies(resp, access_token)
            set_refresh_cookies(resp, refresh_token)

            flash('Connexion réussie !', 'success')
            return resp
        else:
            flash('Email ou mot de passe incorrect', 'error')

    return render_template('login.html')

@app.route('/admin-dashboard')
@jwt_required()
def admin_dashboard():
    current_user_id = get_jwt_identity()
    user = Utilisateur.query.get(int(current_user_id))
    if not user or user.email != 'youssefbensaid839@gmail.com':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('login'))

    return render_template(
    'admin_dashboard.html',
    user=user
)

@app.route('/logout')
def logout():
    resp = redirect(url_for('login'))
    unset_jwt_cookies(resp)
    flash('Déconnexion réussie', 'info')
    return resp
# ==================== FONCTION LOG HISTORIQUE ====================
def log_action(current_user_id, action, entite, entite_id=None, old_value=None, new_value=None):
    """Version robuste pour debug"""
    try:
        current_user_id = int(current_user_id)   # Sécurité

        if action.upper() == "UPDATE" and old_value and new_value:
            details = f"{old_value} → {new_value}"
        elif action.upper() == "CREATE":
            details = f"Création : {new_value or ''}"
        elif action.upper() == "DELETE":
            details = f"Suppression : {old_value or new_value or ''}"
        else:
            details = new_value or old_value or ""

        log = Historique(
            user_id=current_user_id,
            action=action.upper(),
            entite=entite,
            entite_id=entite_id,
            details=details
        )
        db.session.add(log)
        db.session.flush()        # Test si l'insertion passe
        print(f"✅ LOG OK → {action} | {entite} | ID={entite_id}")
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ [HISTORIQUE ERREUR] {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()     # Affiche l'erreur complète dans la console
# Gestion des utilisateurs (accessible depuis admin)
# ===================== GESTION UTILISATEURS =====================
# ===================== GESTION UTILISATEURS =====================
# ═══════════════════════════════════════════════════════════════
# FONCTION D'ENVOI D'EMAIL (SMTP Gmail uniquement)
# ═══════════════════════════════════════════════════════════════
def send_welcome_email(user_email, prenom, email_login, password, role):
    """
    Envoie un email de bienvenue via SMTP Gmail.
    En cas d'échec, affiche l'erreur détaillée dans la console.
    """
    try:
        msg = Message(
            subject="Bienvenue sur la plateforme Outsourcia",
            recipients=[user_email],
            body=f"""
Bonjour {prenom},

Bienvenue sur la plateforme Outsourcia !

🎉 Votre compte a été créé avec succès.

Voici vos identifiants :

📧 Email : {email_login}
🔑 Mot de passe : {password}
👤 Rôle : {role}

👉 Connectez-vous ici : {url_for('login', _external=True)}

⚠️ Pour des raisons de sécurité, changez votre mot de passe dès la première connexion.

Cordialement,
L'équipe Outsourcia – Customer Obsession
""",
            html=f"""
<html>
<body style="font-family:'DM Sans',Arial,sans-serif;background:#f8f9fb;margin:0;padding:0;">
  <div style="max-width:560px;margin:40px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08);">
    <div style="background:linear-gradient(135deg,#f97316,#ea580c);padding:32px 40px;text-align:center;">
      <h1 style="color:white;font-size:22px;font-weight:800;margin:0;">Bienvenue, {prenom} !</h1>
      <p style="color:rgba(255,255,255,.85);font-size:14px;margin-top:8px;">Votre compte a été créé avec succès</p>
    </div>
    <div style="padding:36px 40px;">
      <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:20px 24px;margin-bottom:24px;">
        <div style="margin-bottom:12px;">
          <span style="font-size:12px;font-weight:700;color:#9ca3af;text-transform:uppercase;">Email</span>
          <div style="font-size:15px;font-weight:600;color:#0f1117;margin-top:4px;font-family:monospace;">{email_login}</div>
        </div>
        <div style="margin-bottom:12px;">
          <span style="font-size:12px;font-weight:700;color:#9ca3af;text-transform:uppercase;">Mot de passe</span>
          <div style="font-size:15px;font-weight:600;color:#0f1117;margin-top:4px;font-family:monospace;">{password}</div>
        </div>
        <div>
          <span style="font-size:12px;font-weight:700;color:#9ca3af;text-transform:uppercase;">Rôle</span>
          <div style="margin-top:6px;"><span style="background:#dbeafe;color:#1d4ed8;font-size:12px;font-weight:700;padding:3px 10px;border-radius:6px;">{role}</span></div>
        </div>
      </div>
      <div style="text-align:center;margin-bottom:28px;">
        <a href="{url_for('login', _external=True)}" style="display:inline-block;background:linear-gradient(135deg,#f97316,#ea580c);color:white;font-size:14px;font-weight:700;padding:14px 32px;border-radius:10px;text-decoration:none;box-shadow:0 4px 14px rgba(249,115,22,.35);">
          Se connecter maintenant →
        </a>
      </div>
      <p style="font-size:12px;color:#9ca3af;line-height:1.6;text-align:center;">
        Pour des raisons de sécurité, changez votre mot de passe dès la première connexion.
      </p>
    </div>
    <div style="background:#f8f9fb;border-top:1px solid #e8eaee;padding:16px 40px;text-align:center;">
      <p style="font-size:12px;color:#9ca3af;margin:0;">Outsourcia – Customer Obsession</p>
    </div>
  </div>
</body>
</html>
"""
        )
        mail.send(msg)
        print(f"✅ Email de bienvenue envoyé à {user_email}")
        return True
    except Exception as e:
        print("=" * 60)
        print("❌ ERREUR ENVOI EMAIL SMTP :")
        print(f"   Type : {type(e).__name__}")
        print(f"   Message : {str(e)}")
        print("=" * 60)
        return False
ROLES_AVEC_DO = {'manager', 'responsable_plateau'}

# Rôles qui DOIVENT avoir au moins 1 DO
ROLES_REQUIERENT_DO = ['responsable_plateau', 'manager']

@app.route('/admin/gestion-utilisateurs', methods=['GET', 'POST'])
@jwt_required()
def gestion_utilisateurs():
    current_user_id = int(get_jwt_identity())
    current_user = Utilisateur.query.get(current_user_id)
    donneurs = DonneurdOrdre.query.all()

    if not current_user or current_user.email != 'youssefbensaid839@gmail.com':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')

        # ===================== CREATE =====================
        if action == 'create':
            nom = request.form.get('nom', '').strip()
            prenom = request.form.get('prenom', '').strip()
            numtel = request.form.get('numtel', '').strip()
            email = request.form.get('email', '').strip()
            mot_de_passe = request.form.get('mot_de_passe', '').strip()
            role = request.form.get('role')
            # ✅ Récupère la LISTE des DOs sélectionnés
            do_ids_raw = request.form.getlist('do_ids')

            if not all([nom, prenom, numtel, email, mot_de_passe, role]):
                flash('Tous les champs sont obligatoires', 'error')
                return redirect(url_for('gestion_utilisateurs'))

            if Utilisateur.query.filter_by(email=email).first():
                flash('Cet email est déjà utilisé', 'error')
                return redirect(url_for('gestion_utilisateurs'))

            # ✅ Validation : les rôles manager/responsable DOIVENT avoir au moins 1 DO
            if role in ROLES_REQUIERENT_DO:
                # Nettoie la liste (enlève les vides)
                do_ids = [d for d in do_ids_raw if d.strip()]
                if not do_ids:
                    flash(f'Le rôle "{role}" nécessite au moins un Donneur d\'Ordre.', 'error')
                    return redirect(url_for('gestion_utilisateurs'))
            else:
                # Admin, superviseur... n'ont pas de DO
                do_ids = []

            hashed_password = generate_password_hash(mot_de_passe)

            new_user = Utilisateur(
                nom=nom,
                prenom=prenom,
                numtel=numtel,
                email=email,
                mot_de_passe=hashed_password,
                role=role
            )

            # ✅ Associe les DOs
            for do_id in do_ids:
                do = DonneurdOrdre.query.get(int(do_id))
                if do:
                    new_user.donneurs.append(do)

            db.session.add(new_user)
            db.session.flush()

                        # Envoi email de bienvenue
            email_ok = send_welcome_email(email, prenom, email, mot_de_passe, role)
            if not email_ok:
                flash('Utilisateur créé, mais l\'email de bienvenue n\'a pas pu être envoyé (voir console).', 'warning')

            # Log
            do_noms = ""
            if do_ids:
                do_objs = DonneurdOrdre.query.filter(DonneurdOrdre.id.in_([int(x) for x in do_ids])).all()
                do_noms = " | DO: " + ", ".join([d.nom for d in do_objs])

            log_action(current_user_id, "CREATE", "Utilisateur", entite_id=new_user.id,
                       new_value=f"{nom} {prenom} ({email}) - Rôle: {role}{do_noms}")
            db.session.commit()
            flash('Utilisateur créé avec succès !', 'success')

        # ===================== UPDATE =====================
        elif action == 'update':
            user_id = request.form.get('user_id')
            user = Utilisateur.query.get_or_404(user_id)

            # Ancienne valeur
            old_do_noms = ", ".join([d.nom for d in user.donneurs.all()])
            old_value = f"{user.nom} {user.prenom} | {user.email} | Rôle: {user.role} | Téléphone: {user.numtel}"
            if old_do_noms:
                old_value += f" | DO: {old_do_noms}"

            nom = request.form.get('nom', '').strip()
            prenom = request.form.get('prenom', '').strip()
            numtel = request.form.get('numtel', '').strip()
            email = request.form.get('email', '').strip()
            role = request.form.get('role')
            do_ids_raw = request.form.getlist('do_ids')

            if nom: user.nom = nom
            if prenom: user.prenom = prenom
            if numtel: user.numtel = numtel
            if email: user.email = email
            if role: user.role = role

            # ✅ Gestion des DOs
            if role in ROLES_REQUIERENT_DO:
                do_ids = [d for d in do_ids_raw if d.strip()]
                if not do_ids:
                    flash(f'Le rôle "{role}" nécessite au moins un Donneur d\'Ordre.', 'error')
                    return redirect(url_for('gestion_utilisateurs'))
                # Remplace la liste
                user.donneurs = []
                for do_id in do_ids:
                    do = DonneurdOrdre.query.get(int(do_id))
                    if do:
                        user.donneurs.append(do)
            else:
                user.donneurs = []

            mot_de_passe = request.form.get('mot_de_passe', '').strip()
            if mot_de_passe:
                user.mot_de_passe = generate_password_hash(mot_de_passe)

            # Nouvelle valeur
            new_do_noms = ", ".join([d.nom for d in user.donneurs.all()])
            new_value = f"{user.nom} {user.prenom} | {user.email} | Rôle: {user.role} | Téléphone: {user.numtel}"
            if new_do_noms:
                new_value += f" | DO: {new_do_noms}"

            log_action(current_user_id, "UPDATE", "Utilisateur", entite_id=user.id,
                       old_value=old_value, new_value=new_value)
            db.session.commit()
            flash('Utilisateur modifié avec succès !', 'success')

        # ===================== DELETE =====================
        elif action == 'delete':
            user_id = request.form.get('user_id')
            user = Utilisateur.query.get_or_404(user_id)

            if user.email == 'youssefbensaid839@gmail.com':
                flash('Impossible de supprimer le compte administrateur principal', 'error')
                return redirect(url_for('gestion_utilisateurs'))

            do_noms = ", ".join([d.nom for d in user.donneurs.all()])
            old_value = f"{user.nom} {user.prenom} ({user.email}) - Rôle: {user.role}"
            if do_noms:
                old_value += f" | DO: {do_noms}"

            log_action(current_user_id, "DELETE", "Utilisateur", entite_id=user.id, old_value=old_value)
            db.session.delete(user)
            db.session.commit()
            flash('Utilisateur supprimé avec succès', 'success')

        return redirect(url_for('gestion_utilisateurs'))

    # GET
    utilisateurs = Utilisateur.query.all()
    donneurs = DonneurdOrdre.query.all()
    donneurs_dict = {d.id: d for d in donneurs}

    return render_template('admin_gestion_utilisateurs.html',
                           utilisateurs=utilisateurs,
                           donneurs=donneurs,
                           donneurs_dict=donneurs_dict,
                           user=current_user,
                           ROLES_REQUIERENT_DO=ROLES_REQUIERENT_DO)

# Dashboard Responsable Plateau (vide pour l'instant)
from datetime import datetime

@app.route('/responsable-dashboard')
@jwt_required()
def responsable_dashboard():
    current_user_id = int(get_jwt_identity())
    user = Utilisateur.query.get_or_404(current_user_id)

    if user.role != 'responsable_plateau':
        flash('Accès réservé aux responsables plateau', 'error')
        return redirect(url_for('login'))

    mes_dos = user.donneurs.all()
    
    if not mes_dos:
        flash("Aucun Donneur d'Ordre n'est affecté à votre compte.", 'error')
        return redirect(url_for('login'))

    mes_do_ids = [d.id for d in mes_dos]
    campagnes = Campagne.query.filter(Campagne.do_id.in_(mes_do_ids)).all()
    
    powerbi_base = "https://app.powerbi.com/reportEmbed?reportId=49c058b3-614b-48ec-a925-aa7b067a00a8&autoAuth=true&ctid=604f1a96-cbe8-43f8-abbf-f8eaf5d85730"
    
    # ✅ FILTRE SIMPLE : utiliser l'ID du DO (plus fiable que le nom)
    if len(mes_do_ids) == 1:
        filter_str = f"DonneurdOrdre/id eq {mes_do_ids[0]}"
    else:
        # Plusieurs DOs : utiliser IN
        ids_str = ','.join(str(x) for x in mes_do_ids)
        filter_str = f"DonneurdOrdre/id in ({ids_str})"
    
    # ✅ Ajouter le filtre campagnes si elles existent
    if campagnes:
        camp_ids = ','.join(str(c.id) for c in campagnes)
        filter_str += f" and Campagne/id in ({camp_ids})"
    
    powerbi_url = f"{powerbi_base}&filter={quote(filter_str, safe='()/=, ')}&ts={int(datetime.now().timestamp())}"
    
    print(f"🔗 Power BI URL: {powerbi_url[:200]}...")  # Debug

    return render_template('responsable_dashboard.html', 
                           user=user, 
                           mes_dos=mes_dos,
                           campagnes=campagnes,
                           powerbi_url=powerbi_url)


@app.route('/manager-dashboard')
@jwt_required()
def manager_dashboard():
    current_user_id = int(get_jwt_identity())
    user = Utilisateur.query.get_or_404(current_user_id)

    if user.role != 'manager':
        flash('Accès réservé aux managers', 'error')
        return redirect(url_for('login'))

    mes_dos = user.donneurs.all()
    
    if not mes_dos:
        flash("Aucun Donneur d'Ordre n'est affecté à votre compte.", 'error')
        return redirect(url_for('login'))

    mes_do_ids = [d.id for d in mes_dos]
    campagnes = Campagne.query.filter(Campagne.do_id.in_(mes_do_ids)).all()
    
    powerbi_base = "https://app.powerbi.com/reportEmbed?reportId=49c058b3-614b-48ec-a925-aa7b067a00a8&autoAuth=true&ctid=604f1a96-cbe8-43f8-abbf-f8eaf5d85730"
    
    # FILTRE : DOs du manager
    if len(mes_do_ids) == 1:
        filter_str = f"DonneurdOrdre/id eq {mes_do_ids[0]}"
    else:
        ids_str = ','.join(str(x) for x in mes_do_ids)
        filter_str = f"DonneurdOrdre/id in ({ids_str})"
    
    # Ajouter le filtre campagnes
    if campagnes:
        camp_ids = ','.join(str(c.id) for c in campagnes)
        filter_str += f" and Campagne/id in ({camp_ids})"
    
    powerbi_url = f"{powerbi_base}&filter={quote(filter_str, safe='()/=, ')}&ts={int(datetime.now().timestamp())}"

    return render_template('manager_dashboard.html', 
                           user=user, 
                           mes_dos=mes_dos,
                           campagnes=campagnes,
                           powerbi_url=powerbi_url)
# =====================================================================
# Création de comptes Manager par le Responsable Plateau
# - Rôle forcé : manager
# - DO forcé   : do_id du responsable connecté
# - Envoi email SMTP de bienvenue
# =====================================================================
@app.route('/responsable/creation-comptes', methods=['GET', 'POST'])
@jwt_required()
def responsable_creation_comptes():
    current_user_id = int(get_jwt_identity())
    current_user = Utilisateur.query.get(current_user_id)

    # Vérification : seulement responsable plateau
    if not current_user or current_user.role != 'responsable_plateau':
        flash('Accès réservé aux responsables plateau', 'error')
        return redirect(url_for('responsable_dashboard'))

    # ✅ Récupérer TOUS les DOs du responsable connecté
    mes_dos = current_user.donneurs.all()
    mes_do_ids = [d.id for d in mes_dos]

    # ✅ Récupérer les managers déjà créés par ce responsable
    # (ceux qui partagent au moins un DO avec le responsable)
    managers = Utilisateur.query.filter(
        Utilisateur.role == 'manager',
        Utilisateur.donneurs.any(DonneurdOrdre.id.in_(mes_do_ids))
    ).order_by(Utilisateur.id.desc()).all()

    if request.method == 'POST':
        nom          = request.form.get('nom', '').strip()
        prenom       = request.form.get('prenom', '').strip()
        numtel       = request.form.get('numtel', '').strip()
        email        = request.form.get('email', '').strip()
        mot_de_passe = request.form.get('mot_de_passe', '').strip()
        # ✅ Récupère les DOs sélectionnés
        do_ids_raw   = request.form.getlist('do_ids')

        if not all([nom, prenom, numtel, email, mot_de_passe]):
            flash('Tous les champs sont obligatoires', 'error')
            return redirect(url_for('responsable_creation_comptes'))

        if Utilisateur.query.filter_by(email=email).first():
            flash('Cet email est déjà utilisé', 'error')
            return redirect(url_for('responsable_creation_comptes'))

        # ✅ Validation : au moins 1 DO sélectionné
        do_ids = [int(d) for d in do_ids_raw if d.strip() and int(d) in mes_do_ids]
        if not do_ids:
            flash('Vous devez sélectionner au moins un Donneur d\'Ordre.', 'error')
            return redirect(url_for('responsable_creation_comptes'))

        hashed = generate_password_hash(mot_de_passe)

        nouvel_utilisateur = Utilisateur(
            nom=nom,
            prenom=prenom,
            numtel=numtel,
            email=email,
            mot_de_passe=hashed,
            role='manager'  # ✅ Forcé : manager
        )
        
        # ✅ Associer les DOs sélectionnés
        for do_id in do_ids:
            do = DonneurdOrdre.query.get(do_id)
            if do:
                nouvel_utilisateur.donneurs.append(do)

        db.session.add(nouvel_utilisateur)
        db.session.flush()

        # Log de l'action
        do_noms = ", ".join([d.nom for d in DonneurdOrdre.query.filter(DonneurdOrdre.id.in_(do_ids)).all()])
        log_action(
            current_user_id,
            "CREATE",
            "Utilisateur",
            entite_id=nouvel_utilisateur.id,
            new_value=f"{nom} {prenom} ({email}) - Rôle: manager | DO: {do_noms}"
        )

        # ✅ Envoi email de bienvenue (corrigé)
        # ✅ Envoi email de bienvenue via SMTP Gmail
        try:
            msg = Message(
                subject="Bienvenue sur la plateforme – Votre compte Manager",
                recipients=[email],
                html=f"""
                <html>
                <body style="font-family:'DM Sans',Arial,sans-serif;background:#f8f9fb;margin:0;padding:0;">
                  <div style="max-width:560px;margin:40px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08);">
                    <div style="background:linear-gradient(135deg,#f97316,#ea580c);padding:32px 40px;text-align:center;">
                      <h1 style="color:white;font-size:22px;font-weight:800;margin:0;">Bienvenue, {prenom} !</h1>
                      <p style="color:rgba(255,255,255,.85);font-size:14px;margin-top:8px;">Votre compte Manager a été créé</p>
                    </div>
                    <div style="padding:36px 40px;">
                      <p style="color:#374151;font-size:14px;line-height:1.7;margin-bottom:24px;">
                        Un responsable plateau vous a créé un accès à la plateforme Outsourcia.<br>
                        Voici vos informations de connexion :
                      </p>
                      <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:20px 24px;margin-bottom:24px;">
                        <div style="margin-bottom:12px;">
                          <span style="font-size:12px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;">Email</span>
                          <div style="font-size:15px;font-weight:600;color:#0f1117;margin-top:4px;font-family:monospace;">{email}</div>
                        </div>
                        <div style="margin-bottom:12px;">
                          <span style="font-size:12px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;">Mot de passe</span>
                          <div style="font-size:15px;font-weight:600;color:#0f1117;margin-top:4px;font-family:monospace;">{mot_de_passe}</div>
                        </div>
                        <div style="margin-bottom:12px;">
                          <span style="font-size:12px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;">Rôle</span>
                          <div style="margin-top:6px;"><span style="background:#dbeafe;color:#1d4ed8;font-size:12px;font-weight:700;padding:3px 10px;border-radius:6px;">Manager</span></div>
                        </div>
                        <div style="margin-top:12px;">
                          <span style="font-size:12px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;">Donneur(s) d'Ordre</span>
                          <div style="font-size:14px;font-weight:600;color:#f97316;margin-top:4px;">{do_noms}</div>
                        </div>
                      </div>
                      <div style="text-align:center;margin-bottom:28px;">
                        <a href="{url_for('login', _external=True)}"
                           style="display:inline-block;background:linear-gradient(135deg,#f97316,#ea580c);color:white;font-size:14px;font-weight:700;padding:14px 32px;border-radius:10px;text-decoration:none;box-shadow:0 4px 14px rgba(249,115,22,.35);">
                          Se connecter maintenant →
                        </a>
                      </div>
                      <p style="font-size:12px;color:#9ca3af;line-height:1.6;text-align:center;">
                        Pour des raisons de sécurité, changez votre mot de passe dès la première connexion.<br>
                        Si vous n'êtes pas à l'origine de ce compte, contactez votre responsable.
                      </p>
                    </div>
                    <div style="background:#f8f9fb;border-top:1px solid #e8eaee;padding:16px 40px;text-align:center;">
                      <p style="font-size:12px;color:#9ca3af;margin:0;">Outsourcia – Customer Obsession</p>
                    </div>
                  </div>
                </body>
                </html>
                """
            )
            mail.send(msg)
            db.session.commit()
            flash(f'Compte Manager créé avec succès ! Email de bienvenue envoyé à {email}.', 'success')
        except Exception as e:
            db.session.commit()
            print("=" * 50)
            print("ERREUR ENVOI EMAIL SMTP :")
            print(f"Type: {type(e).__name__}")
            print(f"Message: {str(e)}")
            import traceback
            traceback.print_exc()
            print("=" * 50)
            flash('Compte créé avec succès, mais l\'email de bienvenue n\'a pas pu être envoyé.', 'warning')

        return redirect(url_for('responsable_creation_comptes'))

    return render_template(
        'responsable_creation_comptes.html',
        current_user=current_user,
        mes_dos=mes_dos,           # ✅ Liste des DOs du responsable
        managers=managers
    )

# =========================================================================
# =========================================================================
# GESTION DES DONNEURS D'ORDRE (DO)
# =========================================================================
@app.route('/gestion-donneurs-ordre', methods=['GET', 'POST'])
@jwt_required()
def gestion_donneurs_ordre():
    current_user_id = get_jwt_identity()
    current_user = Utilisateur.query.get(int(current_user_id))
    
    if not current_user or current_user.role != 'admin':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'create':
            donneur = DonneurdOrdre(
                nom=request.form.get('nom'),
                adresse=request.form.get('adresse'),
                telephone=request.form.get('telephone'),
                email=request.form.get('email')
            )
            db.session.add(donneur)
            db.session.flush()
            log_action(current_user_id, "CREATE", "DonneurdOrdre", donneur.id, new_value=donneur.nom)
            db.session.commit()
            flash('Donneur d\'Ordre créé avec succès', 'success')

        elif action == 'update':
            donneur_id = request.form.get('donneur_id')
            donneur = DonneurdOrdre.query.get_or_404(donneur_id)
            old_value = f"{donneur.nom} | {donneur.adresse} | {donneur.email} | {donneur.telephone}"
            
            donneur.nom = request.form.get('nom')
            donneur.adresse = request.form.get('adresse')
            donneur.telephone = request.form.get('telephone')
            donneur.email = request.form.get('email')
            new_value = f"{donneur.nom} | {donneur.adresse} | {donneur.email} | {donneur.telephone}"
            
            log_action(current_user_id, "UPDATE", "DonneurdOrdre", entite_id=donneur.id,
                old_value=old_value,
                new_value=new_value
            )
            db.session.commit()
            flash('Donneur d\'Ordre modifié avec succès', 'success')
        
        elif action == 'delete':
            donneur_id = request.form.get('donneur_id')
            donneur = DonneurdOrdre.query.get_or_404(donneur_id)

            # Sauvegarder pour l'historique
            old_value = f"{donneur.nom} | {donneur.adresse} | {donneur.email} | {donneur.telephone}"

            # ✅ ÉTAPE 1 : Supprimer tous les NPV liés aux campagnes de ce DO
            campagnes_do = Campagne.query.filter_by(do_id=donneur.id).all()
            campagne_ids = [c.id for c in campagnes_do]
            
            npv_count = 0
            if campagne_ids:
                npv_count = NPV.query.filter(NPV.campagne_id.in_(campagne_ids)).delete(synchronize_session='fetch')
            
            # ✅ ÉTAPE 2 : Supprimer aussi les NPV directement liés au DO (do_id)
            npv_direct_count = NPV.query.filter_by(do_id=donneur.id).delete(synchronize_session='fetch')
            
            # ✅ ÉTAPE 3 : Supprimer les campagnes du DO
            campagne_count = Campagne.query.filter_by(do_id=donneur.id).delete(synchronize_session='fetch')
            
            # ✅ ÉTAPE 4 : Supprimer le DO
            db.session.delete(donneur)
            
            # Log détaillé
            total_npv = npv_count + npv_direct_count
            log_details = f"{old_value} | Campagnes supprimées: {campagne_count} | NPV supprimés: {total_npv}"
            log_action(current_user_id, "DELETE", "DonneurdOrdre", entite_id=donneur.id, old_value=log_details)
            
            db.session.commit()
            flash(f'Donneur d\'Ordre supprimé avec succès ({campagne_count} campagnes et {total_npv} NPV associés supprimés)', 'success')

        return redirect(url_for('gestion_donneurs_ordre'))
    
    # GET request
    donneurs = DonneurdOrdre.query.all()
    return render_template('admin_gestion_donneurs_ordre.html', donneurs=donneurs, user=current_user)



# =========================================================================
# GESTION DES CAMPAGNES
# =========================================================================
@app.route('/admin/gestion-campagnes', methods=['GET', 'POST'])
@jwt_required()
def gestion_campagnes():
    current_user_id = int(get_jwt_identity())
    current_user = Utilisateur.query.get(current_user_id)
    if not current_user or current_user.role != 'admin':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')
        campagne_id = request.form.get('campagne_id')

        if action == 'create':
            campagne = Campagne(
                nom=request.form.get('nom'),
                date_debut=request.form.get('date_debut'),
                date_fin=request.form.get('date_fin'),
                do_id=request.form.get('do_id')
            )
            
            log_action(current_user_id, "CREATE", "Campagne", campagne.id, None, campagne.nom)
            db.session.add(campagne)
            db.session.commit()
            flash('Campagne créé avec succès', 'success')
        elif action == 'update':
            campagne = Campagne.query.get(campagne_id)
            if campagne:
                # Ancienne valeur complète
                old_value = f"{campagne.nom} | {campagne.date_debut} → {campagne.date_fin} | DO:{campagne.do_id}"
                
                campagne.nom = request.form.get('nom', campagne.nom)
                campagne.date_debut = request.form.get('date_debut', campagne.date_debut)
                campagne.date_fin = request.form.get('date_fin', campagne.date_fin)
                campagne.do_id = request.form.get('do_id', campagne.do_id)
                
                
                
                # Nouvelle valeur complète
                new_value = f"{campagne.nom} | {campagne.date_debut} → {campagne.date_fin} | DO:{campagne.do_id}"
                
                log_action(current_user_id, "UPDATE", "Campagne", campagne.id, old_value, new_value)
                db.session.commit()
                flash('Campagne modifié avec succès', 'success')

        elif action == 'delete':
            campagne = Campagne.query.get(campagne_id)
            if campagne:
                deleted_info = f"{campagne.nom} (DO: {campagne.do_id})"
                log_action(current_user_id, "DELETE", "Campagne", campagne.id, deleted_info)
                db.session.delete(campagne)
                db.session.commit()
                flash('Campagne supprimé avec succès', 'success')

        return redirect(url_for('gestion_campagnes'))

    campagnes = Campagne.query.options(joinedload(Campagne.do)).all()
    donneurs = DonneurdOrdre.query.all()
    return render_template('admin_gestion_campagnes.html', campagnes=campagnes, donneurs=donneurs, user=current_user )


# ===================== GESTION NPV =====================
# ===================== GESTION NPV =====================
@app.route('/admin/gestion-npv', methods=['GET', 'POST'])
@jwt_required()
def gestion_npv():
    current_user_id = int(get_jwt_identity())
    current_user = Utilisateur.query.get(current_user_id)
    if not current_user or current_user.role != 'admin':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')
        npv_id = request.form.get('npv_id')

        if action == 'create':
            numero = request.form.get('numero', '').strip()
            
            # ✅ Vérifier si le numéro existe déjà
            existant = NPV.query.filter_by(numero=numero).first()
            if existant:
                flash(f'Le numéro {numero} existe déjà.', 'error')
                return redirect(url_for('gestion_npv'))
            
            npv = NPV(
                numero=numero,
                statut=request.form.get('statut'),
                campagne_id=request.form.get('campagne_id') or None,
                operateur_id=request.form.get('operateur_id') or None  # ✅ Ajouté
            )
            
            # ✅ Remplir do_id automatiquement depuis la campagne
            if npv.campagne_id:
                campagne = Campagne.query.get(npv.campagne_id)
                if campagne:
                    npv.do_id = campagne.do_id
            
            log_action(current_user_id, "CREATE", "NPV", npv.id, None, npv.numero)
            db.session.add(npv)
            db.session.commit()
            flash('Npv créé avec succès', 'success')

        elif action == 'update':
            npv = NPV.query.get(npv_id)
            if npv:
                old_value = f"{npv.numero} ({npv.statut}) | Campagne:{npv.campagne_id}"
                
                npv.numero = request.form.get('numero', npv.numero)
                npv.statut = request.form.get('statut', npv.statut)
                npv.campagne_id = request.form.get('campagne_id', npv.campagne_id)
                npv.operateur_id = request.form.get('operateur_id') or None  # ✅ Ajouté
                
                # ✅ Mettre à jour do_id automatiquement
                if npv.campagne_id:
                    campagne = Campagne.query.get(npv.campagne_id)
                    if campagne:
                        npv.do_id = campagne.do_id
                
                new_value = f"{npv.numero} ({npv.statut}) | Campagne:{npv.campagne_id}"
                log_action(current_user_id, "UPDATE", "NPV", npv.id, old_value, new_value)
                db.session.commit()
                flash('Npv modifié avec succès', 'success')

        elif action == 'delete':
            npv = NPV.query.get(npv_id)
            if npv:
                log_action(current_user_id, "DELETE", "NPV", npv.id, npv.numero)
                db.session.delete(npv)
                db.session.commit()
                flash('Npv supprimée avec succès', 'success')

        return redirect(url_for('gestion_npv'))

    npvs = NPV.query.options(joinedload(NPV.campagne)).all()
    campagnes = Campagne.query.all()
    operateurs = Operateur.query.all()  # ✅ Ajouté
    donneurs = DonneurdOrdre.query.all()  # ✅ Pour les filtres
    return render_template('admin_gestion_npv.html', 
                           npvs=npvs, 
                           campagnes=campagnes, 
                           operateurs=operateurs,  # ✅ Ajouté
                           donneurs=donneurs,  # ✅ Pour les filtres
                           user=current_user)

# ===================== GESTION OPÉRATEURS =====================
@app.route('/admin/gestion-operateurs', methods=['GET', 'POST'])
@jwt_required()
def gestion_operateurs():
    current_user_id = int(get_jwt_identity())
    current_user = Utilisateur.query.get(current_user_id)
    if not current_user or current_user.role != 'admin':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')
        operateur_id = request.form.get('operateur_id')

        if action == 'create':
            operateur = Operateur(
                nom=request.form.get('nom'),
                code=request.form.get('code'),
                pays=request.form.get('pays')
            )
            
            log_action(current_user_id, "CREATE", "Operateur", operateur.id, None, operateur.nom)
            db.session.add(operateur)
            db.session.commit()
            flash('Operateur créé avec succès', 'success')

        elif action == 'update':
            operateur = Operateur.query.get(operateur_id)
            if operateur:
                old_value = f"{operateur.nom} ({operateur.code}) | {operateur.pays or '—'}"
                
                operateur.nom = request.form.get('nom', operateur.nom)
                operateur.code = request.form.get('code', operateur.code)
                operateur.pays = request.form.get('pays')
                
                new_value = f"{operateur.nom} ({operateur.code}) | {operateur.pays or '—'}"
                log_action(current_user_id, "UPDATE", "Operateur", operateur.id, old_value, new_value)
                db.session.commit()
                flash('Operateur modifié avec succès', 'success')
             
        elif action == 'delete':
            operateur = Operateur.query.get(operateur_id)
            if operateur:
                # ✅ Supprimer d'abord tous les NPV liés à cet opérateur
                npv_count = NPV.query.filter_by(operateur_id=operateur.id).count()
                if npv_count > 0:
                    NPV.query.filter_by(operateur_id=operateur.id).delete()
                    print(f"   🗑 {npv_count} NPV supprimés (liés à l'opérateur '{operateur.nom}')")
                
                old_value = f"{operateur.nom} ({operateur.code}) | {operateur.pays or '—'} | NPV supprimés: {npv_count}"
                log_action(current_user_id, "DELETE", "Operateur", operateur.id, old_value)
                db.session.delete(operateur)
                db.session.commit()
                flash(f'Opérateur supprimé avec succès ({npv_count} NPV associés supprimés)', 'success')

        return redirect(url_for('gestion_operateurs'))

    operateurs = Operateur.query.all()
    return render_template('admin_gestion_operateurs.html', operateurs=operateurs, user=current_user)

# ===================== HISTORIQUE GLOBAL =====================
@app.route('/admin/historique')
@jwt_required()
def historique():
    current_user_id = int(get_jwt_identity())
    current_user = Utilisateur.query.get(current_user_id)
    utilisateurs = Utilisateur.query.all()
    donneurs = DonneurdOrdre.query.all()

    if not current_user or current_user.email != 'youssefbensaid839@gmail.com':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    # Récupération + jointure avec l'utilisateur pour afficher le nom
    historiques = Historique.query\
        .options(joinedload(Historique.user))\
        .order_by(Historique.date_action.desc())\
        .all()

    return render_template('admin_historique.html', historiques=historiques,user=current_user)
"""
Routes : Gestion Factures + Planificateur d'envoi
==================================================
Fix : l'envoi immédiat depuis le modal sauvegarde d'abord l'email en base
"""

import io, os
from datetime import datetime, timedelta
from flask import render_template, request, send_file, jsonify, flash, redirect, url_for
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask_mail import Mail, Message as MailMessage
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, Image as RLImage
)

LOGO_PATH = os.path.join(os.path.dirname(__file__), 'static', 'logo.png')

C_ORANGE      = colors.HexColor('#f97316')
C_ORANGE_DARK = colors.HexColor('#c2410c')
C_ORANGE_PALE = colors.HexColor('#fff7ed')
C_ORANGE_BDR  = colors.HexColor('#fed7aa')
C_GRAY_900    = colors.HexColor('#111827')
C_GRAY_700    = colors.HexColor('#374151')
C_GRAY_500    = colors.HexColor('#6b7280')
C_GRAY_200    = colors.HexColor('#e5e7eb')
C_GRAY_100    = colors.HexColor('#f3f4f6')
C_WHITE       = colors.white

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta, date
import atexit

# Créer le scheduler
scheduler = BackgroundScheduler()
scheduler.start()
atexit.register(lambda: scheduler.shutdown())
from datetime import timezone, timedelta as td

# Fuseau horaire de Tunis (UTC+1)
TUNIS_TZ = timezone(td(hours=1))

def heure_tunis():
    """Retourne l'heure actuelle à Tunis."""
    return datetime.now(TUNIS_TZ)

def heure_tunis_naive():
    """Retourne l'heure actuelle à Tunis sans timezone (compatible avec la BD)."""
    return datetime.now(TUNIS_TZ).replace(tzinfo=None)


def verifier_et_envoyer_factures_auto():
    """Vérifie les factures planifiées ET récurrentes et les envoie si nécessaire."""
    with app.app_context():
        maintenant = heure_tunis_naive()  # ✅ Heure de Tunis
        maintenant_utc = datetime.utcnow()
        
        print(f"⏰ [SCHEDULER] Vérification à {maintenant.strftime('%H:%M:%S')} (Tunis)")
        
        # ✅ 1. Factures avec récurrence active
        factures_recurrentes = FacturePlanifiee.query.filter(
            FacturePlanifiee.recurrence_active == True,
            FacturePlanifiee.prochain_envoi <= maintenant,
            FacturePlanifiee.envoyee == False
        ).all()
        
        # ✅ 2. Factures planifiées à date fixe (sans récurrence)
        factures_planifiees = FacturePlanifiee.query.filter(
            FacturePlanifiee.recurrence_active == False,
            FacturePlanifiee.date_envoi_planif <= maintenant,
            FacturePlanifiee.date_envoi_planif != None,
            FacturePlanifiee.envoyee == False
        ).all()
        
        print(f"   📊 Récurrentes : {len(factures_recurrentes)} | Planifiées : {len(factures_planifiees)}")
        
        toutes_les_factures = factures_recurrentes + factures_planifiees
        
        for fp in toutes_les_factures:
            try:
                email = fp.email_destinataire
                if not email or email.strip().lower() == 'none':
                    print(f"⚠️ Facture #{fp.id} : aucun email")
                    continue
                
                if fp.recurrence_active:
                    print(f"   🔄 Envoi récurrent #{fp.id} à {email} ({fp.periode_type})")
                else:
                    print(f"   📧 Envoi planifié #{fp.id} à {email} (planifié le {fp.date_envoi_planif})")
                
                maintenant_dt = heure_tunis_naive()
                
                # Déterminer la période
                if fp.recurrence_active and fp.periode_type == '2_minutes':
                    debut = maintenant_dt - timedelta(minutes=2)
                    fin = maintenant_dt
                    prochain = maintenant_dt + timedelta(minutes=2)
                elif fp.recurrence_active and fp.periode_type == 'journaliere':
                    debut = maintenant_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    fin = maintenant_dt.replace(hour=23, minute=59, second=59, microsecond=0)
                    prochain = maintenant_dt + timedelta(days=1)
                elif fp.recurrence_active and fp.periode_type == 'hebdomadaire':
                    debut = maintenant_dt - timedelta(days=7)
                    fin = maintenant_dt
                    prochain = maintenant_dt + timedelta(days=7)
                elif fp.recurrence_active and fp.periode_type == 'mensuelle':
                    debut = maintenant_dt - timedelta(days=30)
                    fin = maintenant_dt
                    prochain = maintenant_dt + timedelta(days=30)
                elif fp.recurrence_active and fp.periode_type == 'trimestrielle':
                    debut = maintenant_dt - timedelta(days=90)
                    fin = maintenant_dt
                    prochain = maintenant_dt + timedelta(days=90)
                else:
                    # Envoi planifié simple
                    debut = fp.date_debut
                    fin = fp.date_fin
                    prochain = None
                
                # Générer le PDF
                do = fp.do
                campagnes = Campagne.query.filter_by(do_id=fp.do_id).all()
                noms_camp = [c.nom for c in campagnes]
                
                from sqlalchemy import func
                montant_total = 0.0
                detail_list = []
                total_appels = 0
                
                if noms_camp:
                    montant_total = db.session.query(
                        func.coalesce(func.sum(CDR.cout_appel_float), 0.0)
                    ).filter(
                        CDR.Campagne.in_(noms_camp),
                        CDR.date_heure_dt >= debut,
                        CDR.date_heure_dt <= fin
                    ).scalar() or 0.0
                    
                    detail_list = db.session.query(
                        CDR.Campagne,
                        func.count(CDR.Campagne).label('nb_appels'),
                        func.coalesce(func.sum(CDR.cout_appel_float), 0.0).label('montant')
                    ).filter(
                        CDR.Campagne.in_(noms_camp),
                        CDR.date_heure_dt >= debut,
                        CDR.date_heure_dt <= fin
                    ).group_by(CDR.Campagne).all()
                    
                    total_appels = sum(r.nb_appels for r in detail_list)
                
                # Utiliser les données existantes si pas de CDR
                if not noms_camp:
                    montant_total = fp.montant_total
                    total_appels = fp.nb_appels
                
                buf = _build_pdf(
                    do=do, date_debut=debut, date_fin=fin,
                    montant_total=float(montant_total),
                    nb_campagnes=len(noms_camp) if noms_camp else fp.nb_campagnes,
                    total_appels=total_appels,
                    detail_list=detail_list,
                    numero_facture=fp.numero_facture
                )
                
                tous_emails = [email]
                if fp.liste_diffusion and fp.liste_diffusion.strip().lower() != 'none':
                    extras = [e.strip() for e in fp.liste_diffusion.split(',') if e.strip()]
                    tous_emails.extend(extras)
                
                nom_fichier = f"Facture_{do.nom.replace(' ','_')}_{debut.strftime('%Y%m%d')}.pdf"
                
                msg = MailMessage(
                    subject=f"Facture {fp.numero_facture} – {do.nom}",
                    recipients=tous_emails,
                    body=f"Bonjour,\n\nVeuillez trouver ci-joint la facture {fp.numero_facture}.\n\nPériode : {debut.strftime('%d/%m/%Y')} – {fin.strftime('%d/%m/%Y')}\nMontant : {float(montant_total):,.4f} €\n\nCordialement,\nOutsourcia"
                )
                buf.seek(0)
                msg.attach(nom_fichier, 'application/pdf', buf.read())
                mail.send(msg)
                
                # Mettre à jour
                fp.envoyee = True
                fp.envoyee_le = heure_tunis_naive()
                
                if fp.recurrence_active:
                    fp.prochain_envoi = prochain
                    fp.envoyee = False  # Réactiver pour le prochain envoi
                
                db.session.commit()
                print(f"   ✅ Facture #{fp.id} envoyée à {email}")
                
            except Exception as e:
                db.session.rollback()
                print(f"   ❌ Erreur facture #{fp.id}: {e}")
                import traceback
                traceback.print_exc()

scheduler.add_job(
    func=verifier_et_envoyer_factures_auto,
    trigger=IntervalTrigger(seconds=30),
    id='envoi_factures_auto',
    name='Envoi automatique des factures',
    replace_existing=True
)
print("✅ SCHEDULER DÉMARRÉ - Vérifie les factures toutes les 30 secondes")
# ╔══════════════════════════════════════════════════════════════╗
# ║  PAGE PRINCIPALE ADMIN                                       ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/admin/gestion-factures')
@jwt_required()
def gestion_factures():
    current_user_id = int(get_jwt_identity())
    user = Utilisateur.query.get(current_user_id)
    if not user or user.email != 'youssefbensaid839@gmail.com':
        flash("Accès réservé à l'administrateur", 'error')
        return redirect(url_for('admin_dashboard'))

    donneurs = DonneurdOrdre.query.order_by(DonneurdOrdre.nom).all()
    factures = FacturePlanifiee.query.order_by(FacturePlanifiee.generee_le.desc()).all()
    return render_template(
    'admin_gestion_factures.html',
    user=user,
    donneurs=donneurs,
    factures=factures
)


# ╔══════════════════════════════════════════════════════════════╗
# ║  PAGE PLANIFICATEUR RESPONSABLE PLATEAU                      ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/responsable/planificateur-factures')
@jwt_required()
def responsable_planificateur_factures():
    current_user_id = int(get_jwt_identity())
    user = Utilisateur.query.get_or_404(current_user_id)

    if user.role != 'responsable_plateau':
        flash('Accès réservé aux responsables plateau', 'error')
        return redirect(url_for('login'))

    # ✅ Récupérer les DOs du responsable
    mes_dos = user.donneurs.all()
    
    if not mes_dos:
        flash("Aucun Donneur d'Ordre n'est affecté à votre compte.", 'error')
        return redirect(url_for('responsable_dashboard'))

    mes_do_ids = [d.id for d in mes_dos]

    # Factures de TOUS les DOs du responsable
    factures = FacturePlanifiee.query.filter(
        FacturePlanifiee.do_id.in_(mes_do_ids)
    ).order_by(FacturePlanifiee.generee_le.desc()).all()

    return render_template(
        'responsable_planificateur_factures.html',
        user=user,
        mes_dos=mes_dos,         # ✅ Liste des DOs (remplace 'do')
        factures=factures
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║  API : Aperçu (AJAX) — partagée admin + responsable          ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/admin/factures/apercu', methods=['POST'])
@jwt_required()
def apercu_facture():
    data       = request.get_json()
    do_id      = data.get('do_id')
    date_debut = datetime.fromisoformat(data.get('date_debut'))
    date_fin   = datetime.fromisoformat(data.get('date_fin'))

    do        = DonneurdOrdre.query.get_or_404(do_id)
    campagnes = Campagne.query.filter_by(do_id=do_id).all()
    noms_camp = [c.nom for c in campagnes]

    from sqlalchemy import func
    montant_total = 0.0
    detail        = []

    if noms_camp:
        montant_total = db.session.query(
            func.coalesce(func.sum(CDR.cout_appel_float), 0.0)
        ).filter(
            CDR.Campagne.in_(noms_camp),
            CDR.date_heure_dt >= date_debut,
            CDR.date_heure_dt <= date_fin
        ).scalar() or 0.0

        detail = db.session.query(
            CDR.Campagne,
            func.count(CDR.Campagne).label('nb_appels'),
            func.coalesce(func.sum(CDR.cout_appel_float), 0.0).label('montant')
        ).filter(
            CDR.Campagne.in_(noms_camp),
            CDR.date_heure_dt >= date_debut,
            CDR.date_heure_dt <= date_fin
        ).group_by(CDR.Campagne).all()

    return jsonify({
        'do_nom':        do.nom,
        'montant_total': round(float(montant_total), 4),
        'nb_campagnes':  len(noms_camp),
        'campagnes': [{
            'nom':       r.Campagne,
            'nb_appels': r.nb_appels,
            'montant':   round(float(r.montant), 4)
        } for r in detail],
        'date_debut': date_debut.strftime('%d/%m/%Y'),
        'date_fin':   date_fin.strftime('%d/%m/%Y'),
    })


# ╔══════════════════════════════════════════════════════════════╗
# ║  GÉNÉRATION PDF + SAUVEGARDE                                 ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/admin/factures/generer', methods=['POST'])
@jwt_required()
def generer_facture():
    do_id      = request.form.get('do_id', type=int)
    date_debut = datetime.fromisoformat(request.form.get('date_debut'))
    date_fin   = datetime.fromisoformat(request.form.get('date_fin'))

    do        = DonneurdOrdre.query.get_or_404(do_id)
    campagnes = Campagne.query.filter_by(do_id=do_id).all()
    noms_camp = [c.nom for c in campagnes]

    from sqlalchemy import func
    montant_total = 0.0
    detail_list   = []
    total_appels  = 0

    if noms_camp:
        montant_total = db.session.query(
            func.coalesce(func.sum(CDR.cout_appel_float), 0.0)
        ).filter(
            CDR.Campagne.in_(noms_camp),
            CDR.date_heure_dt >= date_debut,
            CDR.date_heure_dt <= date_fin
        ).scalar() or 0.0

        detail_list = db.session.query(
            CDR.Campagne,
            func.count(CDR.Campagne).label('nb_appels'),
            func.coalesce(func.sum(CDR.cout_appel_float), 0.0).label('montant')
        ).filter(
            CDR.Campagne.in_(noms_camp),
            CDR.date_heure_dt >= date_debut,
            CDR.date_heure_dt <= date_fin
        ).group_by(CDR.Campagne).all()

        total_appels = sum(r.nb_appels for r in detail_list)

    num_facture = f"FAC-{do_id:04d}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    fp = FacturePlanifiee(
        do_id          = do_id,
        date_debut     = date_debut,
        date_fin       = date_fin,
        montant_total  = float(montant_total),
        nb_campagnes   = len(noms_camp),
        nb_appels      = total_appels,
        numero_facture = num_facture,
        generee_le     = datetime.utcnow(),
    )
    db.session.add(fp)
    db.session.commit()

    buf = _build_pdf(
        do=do, date_debut=date_debut, date_fin=date_fin,
        montant_total=float(montant_total), nb_campagnes=len(noms_camp),
        total_appels=total_appels, detail_list=detail_list,
        numero_facture=num_facture
    )
    nom = f"Facture_{do.nom.replace(' ','_')}_{date_debut.strftime('%Y%m%d')}_{date_fin.strftime('%Y%m%d')}.pdf"
    return send_file(buf, as_attachment=True, download_name=nom, mimetype='application/pdf')


# ╔══════════════════════════════════════════════════════════════╗
# ║  PLANIFIER l'envoi                                           ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/admin/factures/planifier', methods=['POST'])
@jwt_required()
def planifier_facture():
    facture_id      = request.form.get('facture_id', type=int)
    email_principal = request.form.get('email_destinataire', '').strip()
    liste_diffusion = request.form.get('liste_diffusion', '').strip()
    date_envoi_str  = request.form.get('date_envoi', '').strip()
    recurrence      = request.form.get('recurrence', '').strip()
    periode_type    = request.form.get('periode_type', '').strip()

    if not facture_id or not email_principal:
        return jsonify({'success': False, 'error': 'Email destinataire obligatoire'}), 400

    fp = FacturePlanifiee.query.get_or_404(facture_id)
    fp.email_destinataire = email_principal
    fp.liste_diffusion    = liste_diffusion

    if date_envoi_str:
        try:
            # ✅ Parser la date locale (Tunis) et la convertir en naive
            date_envoi = datetime.fromisoformat(date_envoi_str)
            fp.date_envoi_planif = date_envoi  # Garder tel quel (le scheduler compare avec heure_tunis_naive)
        except ValueError:
            pass

    if recurrence == 'on' and periode_type:
        fp.recurrence_active = True
        fp.periode_type = periode_type
        if date_envoi_str:
            fp.prochain_envoi = datetime.fromisoformat(date_envoi_str)
        else:
            fp.prochain_envoi = heure_tunis_naive()
        fp.derniere_generation = heure_tunis_naive()
    else:
        fp.recurrence_active = False
        fp.periode_type = None
        fp.prochain_envoi = None

    db.session.commit()
    
    msg_parts = []
    if fp.date_envoi_planif:
        msg_parts.append(f"Planifié le {fp.date_envoi_planif.strftime('%d/%m/%Y à %H:%M')}")
    if fp.recurrence_active:
        msg_parts.append(f"Récurrence : {fp.periode_type}")
    
    msg = " | ".join(msg_parts) if msg_parts else "Email sauvegardé"
    return jsonify({'success': True, 'message': msg})


scheduler.add_job(
    func=verifier_et_envoyer_factures_auto,
    trigger=IntervalTrigger(seconds=30),
    id='envoi_factures_auto',
    name='Envoi automatique des factures',
    replace_existing=True
)
print("✅ SCHEDULER DÉMARRÉ - Vérifie les factures toutes les 30 secondes (heure de Tunis)")


# ╔══════════════════════════════════════════════════════════════╗
# ║  ENVOYER MAINTENANT                                          ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/admin/factures/envoyer/<int:facture_id>', methods=['POST'])
@jwt_required()
def envoyer_facture(facture_id):
    fp = FacturePlanifiee.query.get_or_404(facture_id)

    # ── Récupérer et nettoyer les emails ─────────────────
    email_form = request.form.get('email_destinataire', '').strip()
    liste_form = request.form.get('liste_diffusion', '').strip()
    
    # Ignorer les 'None' (texte) dans la base
    email_db = ''
    if fp.email_destinataire and fp.email_destinataire.strip().lower() != 'none':
        email_db = fp.email_destinataire.strip()
    
    liste_db = ''
    if fp.liste_diffusion and fp.liste_diffusion.strip().lower() != 'none':
        liste_db = fp.liste_diffusion.strip()
    
    # Priorité : formulaire > base > email du DO
    email = email_form or email_db
    liste = liste_form or liste_db
    
    if not email and fp.do and fp.do.email and fp.do.email.strip().lower() != 'none':
        email = fp.do.email.strip()
    
    if not email:
        return jsonify({
            'success': False,
            'error': f"Aucun email configuré pour '{fp.do.nom}'. Veuillez d'abord ajouter un email au Donneur d'Ordre ou planifier l'envoi avec un email."
        }), 400

    # ── Sauvegarder pour les prochains envois ─────────────
    fp.email_destinataire = email
    if liste:
        fp.liste_diffusion = liste
    db.session.commit()

    # ── Générer le PDF ────────────────────────────────────
    do = fp.do
    campagnes = Campagne.query.filter_by(do_id=fp.do_id).all()
    noms_camp = [c.nom for c in campagnes]

    from sqlalchemy import func
    detail_list = []
    if noms_camp:
        detail_list = db.session.query(
            CDR.Campagne,
            func.count(CDR.Campagne).label('nb_appels'),
            func.coalesce(func.sum(CDR.cout_appel_float), 0.0).label('montant')
        ).filter(
            CDR.Campagne.in_(noms_camp),
            CDR.date_heure_dt >= fp.date_debut,
            CDR.date_heure_dt <= fp.date_fin
        ).group_by(CDR.Campagne).all()

    buf = _build_pdf(
        do=do, date_debut=fp.date_debut, date_fin=fp.date_fin,
        montant_total=fp.montant_total, nb_campagnes=fp.nb_campagnes,
        total_appels=fp.nb_appels, detail_list=detail_list,
        numero_facture=fp.numero_facture
    )

    # ── Construire la liste des destinataires ─────────────
    tous_emails = [email]
    if liste:
        extras = [e.strip() for e in liste.split(',') if e.strip()]
        tous_emails.extend(extras)

    nom_fichier = f"Facture_{do.nom.replace(' ','_')}_{fp.date_debut.strftime('%Y%m%d')}.pdf"

    # ── Envoyer l'email ───────────────────────────────────
    try:
        msg = MailMessage(
            subject    = f"Facture {fp.numero_facture} – {do.nom}",
            recipients = tous_emails,
            body       = (
                f"Bonjour,\n\n"
                f"Veuillez trouver ci-joint la facture {fp.numero_facture} "
                f"concernant le Donneur d'Ordre {do.nom}.\n\n"
                f"Période      : {fp.date_debut.strftime('%d/%m/%Y')} – {fp.date_fin.strftime('%d/%m/%Y')}\n"
                f"Montant total: {fp.montant_total:,.4f} €\n\n"
                f"Cordialement,\nOutsourcia – Service Facturation"
            )
        )
        buf.seek(0)  # ✅ Remettre le buffer au début avant d'attacher
        msg.attach(nom_fichier, 'application/pdf', buf.read())
        mail.send(msg)

        fp.envoyee    = True
        fp.envoyee_le = datetime.utcnow()
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f"Facture envoyée à {', '.join(tous_emails)}"
        })
    except Exception as e:
        import traceback
        print("=" * 60)
        print("❌ ERREUR ENVOI EMAIL FACTURE :")
        print(f"   Type : {type(e).__name__}")
        print(f"   Message : {str(e)}")
        traceback.print_exc()
        print("=" * 60)
        return jsonify({'success': False, 'error': str(e)}), 500


# ╔══════════════════════════════════════════════════════════════╗
# ║  RE-TÉLÉCHARGER                                              ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/admin/factures/telecharger/<int:facture_id>')
@jwt_required()
def telecharger_facture(facture_id):
    fp        = FacturePlanifiee.query.get_or_404(facture_id)
    do        = fp.do
    campagnes = Campagne.query.filter_by(do_id=fp.do_id).all()
    noms_camp = [c.nom for c in campagnes]

    from sqlalchemy import func
    detail_list = []
    if noms_camp:
        detail_list = db.session.query(
            CDR.Campagne,
            func.count(CDR.Campagne).label('nb_appels'),
            func.coalesce(func.sum(CDR.cout_appel_float), 0.0).label('montant')
        ).filter(
            CDR.Campagne.in_(noms_camp),
            CDR.date_heure_dt >= fp.date_debut,
            CDR.date_heure_dt <= fp.date_fin
        ).group_by(CDR.Campagne).all()

    buf = _build_pdf(
        do=do, date_debut=fp.date_debut, date_fin=fp.date_fin,
        montant_total=fp.montant_total, nb_campagnes=fp.nb_campagnes,
        total_appels=fp.nb_appels, detail_list=detail_list,
        numero_facture=fp.numero_facture
    )
    nom = f"Facture_{do.nom.replace(' ','_')}_{fp.date_debut.strftime('%Y%m%d')}.pdf"
    return send_file(buf, as_attachment=True, download_name=nom, mimetype='application/pdf')


# ╔══════════════════════════════════════════════════════════════╗
# ║  SUPPRIMER                                                   ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/admin/factures/supprimer/<int:facture_id>', methods=['POST'])
@jwt_required()
def supprimer_facture(facture_id):
    fp = FacturePlanifiee.query.get_or_404(facture_id)
    db.session.delete(fp)
    db.session.commit()
    return jsonify({'success': True})


# ╔══════════════════════════════════════════════════════════════╗
# ║  GÉNÉRATION PDF PREMIUM                                      ║
# ╚══════════════════════════════════════════════════════════════╝
def _build_pdf(do, date_debut, date_fin, montant_total, nb_campagnes,
               total_appels, detail_list, numero_facture):
    buf = io.BytesIO()
    W, H = A4
    marge = 2 * cm
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=marge, rightMargin=marge,
        topMargin=1.5*cm, bottomMargin=2*cm,
        title=f"Facture {numero_facture}"
    )
    UW = W - 2 * marge

    def sty(name, **kw):
        s = getSampleStyleSheet()['Normal'].clone(name)
        for k, v in kw.items(): setattr(s, k, v)
        return s

    s_h1       = sty('H1',      fontName='Helvetica-Bold', fontSize=26, textColor=C_ORANGE, leading=30)
    s_label    = sty('Lbl',     fontName='Helvetica-Bold', fontSize=7,  textColor=C_GRAY_500, leading=9)
    s_val      = sty('Val',     fontName='Helvetica',      fontSize=10, textColor=C_GRAY_900, leading=13)
    s_val_or   = sty('ValOr',   fontName='Helvetica-Bold', fontSize=11, textColor=C_ORANGE,   leading=14)
    s_do_name  = sty('DON',     fontName='Helvetica-Bold', fontSize=13, textColor=C_GRAY_900, leading=16)
    s_num_fac  = sty('NF',      fontName='Helvetica-Bold', fontSize=9,  textColor=C_ORANGE,   leading=11)
    s_periode  = sty('Per',     fontName='Helvetica-Bold', fontSize=10, textColor=C_GRAY_900, leading=13)
    s_th       = sty('TH',      fontName='Helvetica-Bold', fontSize=8,  textColor=C_WHITE,    leading=10)
    s_th_r     = sty('THR',     fontName='Helvetica-Bold', fontSize=8,  textColor=C_WHITE,    leading=10, alignment=TA_RIGHT)
    s_td       = sty('TD',      fontName='Helvetica',      fontSize=8,  textColor=C_GRAY_700, leading=11)
    s_td_r     = sty('TDR',     fontName='Helvetica',      fontSize=8,  textColor=C_GRAY_700, leading=11, alignment=TA_RIGHT)
    s_td_br    = sty('TDBR',    fontName='Helvetica-Bold', fontSize=8,  textColor=C_GRAY_900, leading=11, alignment=TA_RIGHT)
    s_st       = sty('ST',      fontName='Helvetica-Bold', fontSize=8,  textColor=C_GRAY_900, leading=11)
    s_st_r     = sty('STR',     fontName='Helvetica-Bold', fontSize=8,  textColor=C_ORANGE_DARK, leading=11, alignment=TA_RIGHT)
    s_tot_lbl  = sty('TotL',    fontName='Helvetica-Bold', fontSize=11, textColor=C_WHITE,    leading=14)
    s_tot_val  = sty('TotV',    fontName='Helvetica-Bold', fontSize=20, textColor=C_WHITE,    leading=24, alignment=TA_RIGHT)
    s_tva      = sty('TVA',     fontName='Helvetica',      fontSize=8,  textColor=colors.HexColor('#fed7aa'), leading=10)
    s_footer   = sty('Ftr',     fontName='Helvetica',      fontSize=7,  textColor=C_GRAY_500, alignment=TA_CENTER, leading=10)
    s_fbrand   = sty('FBrand',  fontName='Helvetica-Bold', fontSize=8,  textColor=C_ORANGE,   leading=10)
    s_fconf    = sty('FConf',   fontName='Helvetica-Oblique', fontSize=7, textColor=C_GRAY_500, leading=9, alignment=TA_RIGHT)
    s_sec      = sty('Sec',     fontName='Helvetica-Bold', fontSize=10, textColor=C_ORANGE_DARK, leading=13, spaceBefore=12, spaceAfter=4)

    story = []

    # ── En-tête : logo + FACTURE + N° ────────────────────────────────
    logo_cell = ''
    if os.path.exists(LOGO_PATH):
        logo_cell = RLImage(LOGO_PATH, width=3.0*cm, height=3.0*cm)

    ref_bloc = Table([
        [Paragraph('N° Facture', s_label)],
        [Paragraph(numero_facture, s_num_fac)],
        [Spacer(1, 4)],
        [Paragraph("Date d'émission", s_label)],
        [Paragraph(datetime.now().strftime('%d/%m/%Y  %H:%M'), s_val)],
    ], colWidths=[UW*0.30],
       style=TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),1)]))

    top = Table([[logo_cell, Paragraph('FACTURE', s_h1), ref_bloc]],
                colWidths=[UW*0.18, UW*0.37, UW*0.45])
    top.setStyle(TableStyle([
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('ALIGN',(2,0),(2,0),'RIGHT'),
        ('BOTTOMPADDING',(0,0),(-1,-1),4),
    ]))
    story.append(top)
    story.append(HRFlowable(width=UW, thickness=4, color=C_ORANGE, lineCap='round', spaceAfter=14))

    # ── Blocs DO + Période ────────────────────────────────────────────
    def info_bloc(titre, contenu_rows, w, bg=C_ORANGE_PALE, bdr=C_ORANGE_BDR):
        rows = [[Paragraph(titre, sty('BT2', fontName='Helvetica-Bold', fontSize=7,
                                       textColor=C_ORANGE_DARK, leading=9))]]
        for r in contenu_rows: rows.append([r])
        t = Table(rows, colWidths=[w])
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,-1),bg), ('BOX',(0,0),(-1,-1),0.8,bdr),
            ('LEFTPADDING',(0,0),(-1,-1),10), ('RIGHTPADDING',(0,0),(-1,-1),10),
            ('TOPPADDING',(0,0),(-1,-1),8),   ('BOTTOMPADDING',(0,0),(-1,-1),8),
        ]))
        return t

    periode_str = f"{date_debut.strftime('%d/%m/%Y')}  →  {date_fin.strftime('%d/%m/%Y')}"

    stats_row = Table([[
        Table([[Paragraph('CAMPAGNES',s_label)],[Paragraph(str(nb_campagnes),s_val_or)]],
               style=TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),1)])),
        Table([[Paragraph('TOTAL APPELS',s_label)],[Paragraph(f'{total_appels:,}',s_val_or)]],
               style=TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),1)])),
    ]], colWidths=[UW*0.20, UW*0.22],
    style=TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))

    bloc_do  = info_bloc("FACTURÉ À", [
        Paragraph(f'<b>{do.nom}</b>', s_do_name),
        Paragraph(do.email    or '—', s_val),
        Paragraph(do.telephone or '—', s_val),
        Paragraph(do.adresse  or '—', s_val),
    ], UW*0.46)

    bloc_per = info_bloc("PÉRIODE & RÉSUMÉ", [
        Paragraph(f'<b>{periode_str}</b>', s_periode),
        Spacer(1, 6), stats_row,
    ], UW*0.46, bg=C_GRAY_100, bdr=C_GRAY_200)

    story.append(Table([[bloc_do, Spacer(UW*0.08,1), bloc_per]],
                        colWidths=[UW*0.46, UW*0.08, UW*0.46],
                        style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP')])))
    story.append(Spacer(1, 18))

    # ── Tableau campagnes ─────────────────────────────────────────────
    story.append(Paragraph('Détail des campagnes', s_sec))
    story.append(HRFlowable(width=UW, thickness=1, color=C_ORANGE_BDR, spaceAfter=6))

    if detail_list:
        rows = [[
            Paragraph('CAMPAGNE',          s_th),
            Paragraph('NB APPELS',         s_th_r),
            Paragraph('COÛT MOY. / APPEL', s_th_r),
            Paragraph('MONTANT (€)',        s_th_r),
        ]]
        for i, row in enumerate(detail_list):
            moy = float(row.montant)/row.nb_appels if row.nb_appels else 0
            rows.append([
                Paragraph(row.Campagne or '—', s_td),
                Paragraph(f'{row.nb_appels:,}',        s_td_r),
                Paragraph(f'{moy:.4f} €',              s_td_r),
                Paragraph(f'{float(row.montant):,.4f} €', s_td_br),
            ])

        rows.append([
            Paragraph('<b>SOUS-TOTAL</b>', s_st),
            Paragraph(f'<b>{total_appels:,}</b>', sty('STA', fontName='Helvetica-Bold',
                       fontSize=8, textColor=C_GRAY_900, leading=11, alignment=TA_RIGHT)),
            Paragraph('', s_td_r),
            Paragraph(f'<b>{montant_total:,.4f} €</b>', s_st_r),
        ])

        t = Table(rows, colWidths=[UW*0.42, UW*0.16, UW*0.22, UW*0.20])
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,0),  C_ORANGE),
            ('ROWBACKGROUNDS',(0,1),(-1,-2), [C_ORANGE_PALE, C_WHITE]),
            ('BACKGROUND',    (0,-1),(-1,-1),C_GRAY_100),
            ('LINEABOVE',     (0,-1),(-1,-1),1, C_ORANGE_BDR),
            ('GRID',          (0,0),(-1,-2), 0.3, C_GRAY_200),
            ('TOPPADDING',    (0,0),(-1,-1), 5),
            ('BOTTOMPADDING', (0,0),(-1,-1), 5),
            ('LEFTPADDING',   (0,0),(-1,-1), 7),
            ('RIGHTPADDING',  (0,0),(-1,-1), 7),
            ('VALIGN',        (0,0),(-1,-1), 'MIDDLE'),
        ]))
        story.append(t)
    else:
        story.append(Paragraph('Aucun appel enregistré sur cette période.',
                                sty('V', fontName='Helvetica-Oblique', fontSize=9, textColor=C_GRAY_500)))

    story.append(Spacer(1, 20))

    # ── Montant total ─────────────────────────────────────────────────
    tot = Table([[
        Table([[Paragraph('MONTANT TOTAL HT', s_tot_lbl)],
               [Paragraph('TVA non applicable', s_tva)]],
               style=TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),1)])),
        Paragraph(f'{montant_total:,.4f} €', s_tot_val),
    ]], colWidths=[UW*0.55, UW*0.45])
    tot.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),C_ORANGE),
        ('LEFTPADDING',(0,0),(-1,-1),20), ('RIGHTPADDING',(0,0),(-1,-1),20),
        ('TOPPADDING',(0,0),(-1,-1),16),  ('BOTTOMPADDING',(0,0),(-1,-1),16),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'), ('ALIGN',(1,0),(1,0),'RIGHT'),
    ]))
    story.append(KeepTogether([tot]))
    story.append(Spacer(1, 24))

    # ── Pied de page ──────────────────────────────────────────────────
    story.append(HRFlowable(width=UW, thickness=0.5, color=C_GRAY_200, spaceAfter=8))
    story.append(Table([[
        Paragraph('Outsourcia – Customer Obsession', s_fbrand),
        Paragraph(f'Document généré le {datetime.now().strftime("%d/%m/%Y à %H:%M")}  •  Réf. {numero_facture}', s_footer),
        Paragraph('Document confidentiel', s_fconf),
    ]], colWidths=[UW*0.35, UW*0.40, UW*0.25],
    style=TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                       ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)])))

    doc.build(story)
    buf.seek(0)
    return buf
# ╔══════════════════════════════════════════════════════════════╗
# ║  PAGE PLANIFICATEUR MANAGER                                  ║
# ║  - Factures filtrées sur son DO uniquement                   ║
# ║  - Envoi email vers son propre email uniquement              ║
# ║  - Pas de champ email modifiable / pas de liste diffusion    ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/manager/planificateur-factures')
@jwt_required()
def manager_planificateur_factures():
    current_user_id = int(get_jwt_identity())
    user = Utilisateur.query.get_or_404(current_user_id)

    if user.role != 'manager':
        flash('Accès réservé aux managers', 'error')
        return redirect(url_for('login'))

    # ✅ Récupérer les DOs du manager
    mes_dos = user.donneurs.all()
    
    if not mes_dos:
        flash("Aucun Donneur d'Ordre affecté à votre compte.", 'error')
        return redirect(url_for('manager_dashboard'))

    mes_do_ids = [d.id for d in mes_dos]

    # Factures de TOUS les DOs du manager
    factures = FacturePlanifiee.query.filter(
        FacturePlanifiee.do_id.in_(mes_do_ids)
    ).order_by(FacturePlanifiee.generee_le.desc()).all()

    return render_template(
        'manager_planificateur_factures.html',
        user=user,
        mes_dos=mes_dos,
        factures=factures
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║  PLANIFIER ENVOI MANAGER                                     ║
# ║  - Sauvegarde la date d'envoi                                ║
# ║  - Email forcé = email du manager connecté                   ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/manager/factures/planifier', methods=['POST'])
@jwt_required()
def manager_planifier_facture():
    current_user_id = int(get_jwt_identity())
    user = Utilisateur.query.get_or_404(current_user_id)

    if user.role != 'manager':
        return jsonify({'success': False, 'error': 'Accès refusé'}), 403

    facture_id     = request.form.get('facture_id', type=int)
    date_envoi_str = request.form.get('date_envoi', '').strip()
    # ✅ Récupérer la récurrence
    recurrence     = request.form.get('recurrence', '').strip()
    periode_type   = request.form.get('periode_type', '').strip()

    if not facture_id:
        return jsonify({'success': False, 'error': 'Facture introuvable'}), 400

    fp = FacturePlanifiee.query.get_or_404(facture_id)

    # Vérifier que la facture appartient bien à un DO du manager
    mes_do_ids = [d.id for d in user.donneurs.all()]
    if fp.do_id not in mes_do_ids:
        return jsonify({'success': False, 'error': 'Accès refusé à cette facture'}), 403

    # Email forcé = email du manager
    fp.email_destinataire = user.email
    fp.liste_diffusion    = ''

    if date_envoi_str:
        try:
            fp.date_envoi_planif = datetime.fromisoformat(date_envoi_str)
        except ValueError:
            return jsonify({'success': False, 'error': 'Format de date invalide'}), 400

    # ✅ Gérer la récurrence
    if recurrence == 'on' and periode_type:
        fp.recurrence_active = True
        fp.periode_type = periode_type
        if date_envoi_str:
            fp.prochain_envoi = datetime.fromisoformat(date_envoi_str)
        else:
            fp.prochain_envoi = datetime.utcnow()
        fp.derniere_generation = datetime.utcnow()
    else:
        fp.recurrence_active = False
        fp.periode_type = None
        fp.prochain_envoi = None

    db.session.commit()

    msg_parts = []
    if fp.date_envoi_planif:
        msg_parts.append(f"Envoi planifié le {fp.date_envoi_planif.strftime('%d/%m/%Y à %H:%M')}")
    if fp.recurrence_active:
        msg_parts.append(f"Récurrence : {fp.periode_type}")
    
    msg = " | ".join(msg_parts) if msg_parts else "Email sauvegardé"
    return jsonify({'success': True, 'message': msg})


# ╔══════════════════════════════════════════════════════════════╗
# ║  ENVOYER MAINTENANT MANAGER                                  ║
# ║  - Sauvegarde l'email du manager en base puis envoie         ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/manager/factures/envoyer/<int:facture_id>', methods=['POST'])
@jwt_required()
def manager_envoyer_facture(facture_id):
    current_user_id = int(get_jwt_identity())
    user = Utilisateur.query.get_or_404(current_user_id)

    if user.role != 'manager':
        return jsonify({'success': False, 'error': 'Accès refusé'}), 403

    fp = FacturePlanifiee.query.get_or_404(facture_id)

    # ✅ Récupérer les IDs des DOs du manager
    mes_do_ids = [d.id for d in user.donneurs.all()]
    
    if fp.do_id not in mes_do_ids:
        return jsonify({'success': False, 'error': 'Accès refusé à cette facture'}), 403

    # ... (le reste du code est inchangé)

    # ✅ Forcer l'email = email du manager (sauvegarde en base avant envoi)
    fp.email_destinataire = user.email
    fp.liste_diffusion    = ''
    db.session.commit()

    do        = fp.do
    campagnes = Campagne.query.filter_by(do_id=fp.do_id).all()
    noms_camp = [c.nom for c in campagnes]

    from sqlalchemy import func
    detail_list = []
    if noms_camp:
        detail_list = db.session.query(
            CDR.Campagne,
            func.count(CDR.Campagne).label('nb_appels'),
            func.coalesce(func.sum(CDR.cout_appel_float), 0.0).label('montant')
        ).filter(
            CDR.Campagne.in_(noms_camp),
            CDR.date_heure_dt >= fp.date_debut,
            CDR.date_heure_dt <= fp.date_fin
        ).group_by(CDR.Campagne).all()

    buf = _build_pdf(
        do=do,
        date_debut=fp.date_debut,
        date_fin=fp.date_fin,
        montant_total=fp.montant_total,
        nb_campagnes=fp.nb_campagnes,
        total_appels=fp.nb_appels,
        detail_list=detail_list,
        numero_facture=fp.numero_facture
    )

    nom_fichier = f"Facture_{do.nom.replace(' ','_')}_{fp.date_debut.strftime('%Y%m%d')}.pdf"

    try:
        msg = MailMessage(
            subject    = f"Facture {fp.numero_facture} – {do.nom}",
            recipients = [user.email],   # ✅ uniquement son propre email
            body       = (
                f"Bonjour {user.prenom} {user.nom},\n\n"
                f"Veuillez trouver ci-joint votre facture {fp.numero_facture} "
                f"concernant le Donneur d'Ordre {do.nom}.\n\n"
                f"Période      : {fp.date_debut.strftime('%d/%m/%Y')} – "
                f"{fp.date_fin.strftime('%d/%m/%Y')}\n"
                f"Montant total: {fp.montant_total:,.4f} €\n\n"
                f"Cordialement,\nOutsourcia – Service Facturation"
            )
        )
        msg.attach(nom_fichier, 'application/pdf', buf.read())
        mail.send(msg)

        fp.envoyee    = True
        fp.envoyee_le = datetime.utcnow()
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f"Facture envoyée à {user.email}"
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ╔══════════════════════════════════════════════════════════════╗
# ║  TÉLÉCHARGER (manager)                                       ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/manager/factures/telecharger/<int:facture_id>')
@jwt_required()
def manager_telecharger_facture(facture_id):
    current_user_id = int(get_jwt_identity())
    user = Utilisateur.query.get_or_404(current_user_id)

    if user.role != 'manager':
        return jsonify({'success': False, 'error': 'Accès refusé'}), 403

    fp = FacturePlanifiee.query.get_or_404(facture_id)

    # ✅ Récupérer les IDs des DOs du manager
    mes_do_ids = [d.id for d in user.donneurs.all()]
    
    if fp.do_id not in mes_do_ids:
        return jsonify({'success': False, 'error': 'Accès refusé'}), 403

    # ... (le reste du code est inchangé)
    do        = fp.do
    campagnes = Campagne.query.filter_by(do_id=fp.do_id).all()
    noms_camp = [c.nom for c in campagnes]

    from sqlalchemy import func
    detail_list = []
    if noms_camp:
        detail_list = db.session.query(
            CDR.Campagne,
            func.count(CDR.Campagne).label('nb_appels'),
            func.coalesce(func.sum(CDR.cout_appel_float), 0.0).label('montant')
        ).filter(
            CDR.Campagne.in_(noms_camp),
            CDR.date_heure_dt >= fp.date_debut,
            CDR.date_heure_dt <= fp.date_fin
        ).group_by(CDR.Campagne).all()

    buf = _build_pdf(
        do=do,
        date_debut=fp.date_debut,
        date_fin=fp.date_fin,
        montant_total=fp.montant_total,
        nb_campagnes=fp.nb_campagnes,
        total_appels=fp.nb_appels,
        detail_list=detail_list,
        numero_facture=fp.numero_facture
    )
    nom = f"Facture_{do.nom.replace(' ','_')}_{fp.date_debut.strftime('%Y%m%d')}.pdf"
    return send_file(buf, as_attachment=True, download_name=nom, mimetype='application/pdf')


# ╔══════════════════════════════════════════════════════════════╗
# ║  GÉNÉRATION PDF (manager — réutilise la route admin)         ║
# ╚══════════════════════════════════════════════════════════════╝
@app.route('/manager/factures/generer', methods=['POST'])
@jwt_required()
def manager_generer_facture():
    current_user_id = int(get_jwt_identity())
    user = Utilisateur.query.get_or_404(current_user_id)

    if user.role != 'manager':
        return jsonify({'success': False, 'error': 'Accès refusé'}), 403

    # ✅ Récupérer le premier DO du manager (pour la génération)
    premier_do = user.donneurs.first()
    if not premier_do:
        return jsonify({'success': False, 'error': 'Aucun DO affecté'}), 400
    
    do_id = premier_do.id

    date_debut = datetime.fromisoformat(request.form.get('date_debut'))
    date_fin   = datetime.fromisoformat(request.form.get('date_fin'))

    do        = DonneurdOrdre.query.get_or_404(do_id)
    campagnes = Campagne.query.filter_by(do_id=do_id).all()
    noms_camp = [c.nom for c in campagnes]

    # ... (le reste du code est inchangé)

    from sqlalchemy import func
    montant_total = 0.0
    detail_list   = []
    total_appels  = 0

    if noms_camp:
        montant_total = db.session.query(
            func.coalesce(func.sum(CDR.cout_appel_float), 0.0)
        ).filter(
            CDR.Campagne.in_(noms_camp),
            CDR.date_heure_dt >= date_debut,
            CDR.date_heure_dt <= date_fin
        ).scalar() or 0.0

        detail_list = db.session.query(
            CDR.Campagne,
            func.count(CDR.Campagne).label('nb_appels'),
            func.coalesce(func.sum(CDR.cout_appel_float), 0.0).label('montant')
        ).filter(
            CDR.Campagne.in_(noms_camp),
            CDR.date_heure_dt >= date_debut,
            CDR.date_heure_dt <= date_fin
        ).group_by(CDR.Campagne).all()

        total_appels = sum(r.nb_appels for r in detail_list)

    num_facture = f"FAC-{do_id:04d}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    fp = FacturePlanifiee(
        do_id             = do_id,
        date_debut        = date_debut,
        date_fin          = date_fin,
        montant_total     = float(montant_total),
        nb_campagnes      = len(noms_camp),
        nb_appels         = total_appels,
        numero_facture    = num_facture,
        generee_le        = datetime.utcnow(),
        # ✅ Email pré-rempli avec l'email du manager dès la création
        email_destinataire= user.email,
        liste_diffusion   = '',
    )
    db.session.add(fp)
    db.session.commit()

    buf = _build_pdf(
        do=do, date_debut=date_debut, date_fin=date_fin,
        montant_total=float(montant_total), nb_campagnes=len(noms_camp),
        total_appels=total_appels, detail_list=detail_list,
        numero_facture=num_facture
    )
    nom = f"Facture_{do.nom.replace(' ','_')}_{date_debut.strftime('%Y%m%d')}_{date_fin.strftime('%Y%m%d')}.pdf"
    return send_file(buf, as_attachment=True, download_name=nom, mimetype='application/pdf')



if __name__ == '__main__':
    app.run(debug=True)