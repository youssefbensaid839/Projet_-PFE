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

# Modèle Utilisateur
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
    do_id = db.Column(db.Integer, db.ForeignKey('DonneurdOrdre.id'), nullable=True)
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
    

    # Ajoute cette ligne → c'est elle qui crée l'attribut .do
    do = db.relationship('DonneurdOrdre', backref='campagnes', lazy=True)

    # Optionnel : relation inverse pour les NPV si tu veux
    npvs = db.relationship('NPV', backref='campagne', lazy=True)

class NPV(db.Model):
    __tablename__ = 'NPV'
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(50), unique=True, nullable=False)
    statut = db.Column(db.String(50))
    campagne_id = db.Column(db.Integer, db.ForeignKey('Campagne.id'), nullable=False)

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

    return render_template('admin_dashboard.html')

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

ROLES_AVEC_DO = {'manager', 'responsable_plateau'}

@app.route('/admin/gestion-utilisateurs', methods=['GET', 'POST'])
@jwt_required()
def gestion_utilisateurs():
    current_user_id = int(get_jwt_identity())
    current_user = Utilisateur.query.get(current_user_id)

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
            do_id_raw = request.form.get('do_id')

            if not all([nom, prenom, numtel, email, mot_de_passe, role]):
                flash('Tous les champs sont obligatoires', 'error')
                return redirect(url_for('gestion_utilisateurs'))

            if Utilisateur.query.filter_by(email=email).first():
                flash('Cet email est déjà utilisé', 'error')
                return redirect(url_for('gestion_utilisateurs'))

            # Gestion du DO
            do_id = None
            if role in ROLES_AVEC_DO:
                if do_id_raw and do_id_raw.strip():
                    do_id = int(do_id_raw)
                else:
                    flash('Vous devez sélectionner un Donneur d\'Ordre pour ce rôle.', 'error')
                    return redirect(url_for('gestion_utilisateurs'))

            hashed_password = generate_password_hash(mot_de_passe)

            new_user = Utilisateur(
                nom=nom,
                prenom=prenom,
                numtel=numtel,
                email=email,
                mot_de_passe=hashed_password,
                role=role,
                do_id=do_id
            )

            db.session.add(new_user)
            db.session.flush()

            do_nom = ""
            if do_id:
                do_obj = DonneurdOrdre.query.get(do_id)
                do_nom = f" | DO: {do_obj.nom}" if do_obj else ""

            log_action(
                current_user_id,
                "CREATE",
                "Utilisateur",
                entite_id=new_user.id,
                new_value=f"{nom} {prenom} ({email}) - Rôle: {role}{do_nom}"
            )
            db.session.commit()
            flash('Utilisateur créé avec succès !', 'success')

        # ===================== UPDATE =====================
        elif action == 'update':
            user_id = request.form.get('user_id')
            user = Utilisateur.query.get_or_404(user_id)

            old_do_nom = ""
            if user.do_id:
                old_do_obj = DonneurdOrdre.query.get(user.do_id)
                old_do_nom = f" | DO: {old_do_obj.nom}" if old_do_obj else ""

            old_value = f"{user.nom} {user.prenom} | {user.email} | Rôle: {user.role} | Téléphone: {user.numtel}{old_do_nom}"

            nom = request.form.get('nom', '').strip()
            prenom = request.form.get('prenom', '').strip()
            numtel = request.form.get('numtel', '').strip()
            email = request.form.get('email', '').strip()
            role = request.form.get('role')
            do_id_raw = request.form.get('do_id')

            if nom: user.nom = nom
            if prenom: user.prenom = prenom
            if numtel: user.numtel = numtel
            if email: user.email = email
            if role: user.role = role

            if role in ROLES_AVEC_DO:
                if do_id_raw and do_id_raw.strip():
                    user.do_id = int(do_id_raw)
            else:
                user.do_id = None

            mot_de_passe = request.form.get('mot_de_passe', '').strip()
            if mot_de_passe:
                user.mot_de_passe = generate_password_hash(mot_de_passe)

            new_do_nom = ""
            if user.do_id:
                new_do_obj = DonneurdOrdre.query.get(user.do_id)
                new_do_nom = f" | DO: {new_do_obj.nom}" if new_do_obj else ""

            new_value = f"{user.nom} {user.prenom} | {user.email} | Rôle: {user.role} | Téléphone: {user.numtel}{new_do_nom}"

            log_action(
                current_user_id,
                "UPDATE",
                "Utilisateur",
                entite_id=user.id,
                old_value=old_value,
                new_value=new_value
            )
            db.session.commit()
            flash('Utilisateur modifié avec succès !', 'success')

        # ===================== DELETE =====================
        elif action == 'delete':
            user_id = request.form.get('user_id')
            user = Utilisateur.query.get_or_404(user_id)

            if user.email == 'youssefbensaid839@gmail.com':
                flash('Impossible de supprimer le compte administrateur principal', 'error')
                return redirect(url_for('gestion_utilisateurs'))

            do_nom = ""
            if user.do_id:
                do_obj = DonneurdOrdre.query.get(user.do_id)
                do_nom = f" | DO: {do_obj.nom}" if do_obj else ""

            old_value = f"{user.nom} {user.prenom} ({user.email}) - Rôle: {user.role}{do_nom}"

            log_action(
                current_user_id,
                "DELETE",
                "Utilisateur",
                entite_id=user.id,
                old_value=old_value
            )

            db.session.delete(user)
            db.session.commit()
            flash('Utilisateur supprimé avec succès', 'success')

        return redirect(url_for('gestion_utilisateurs'))

    # GET - Affichage liste
    utilisateurs = Utilisateur.query.all()
    donneurs = DonneurdOrdre.query.all()

    # Construire un dictionnaire do_id → objet DonneurdOrdre pour accès rapide dans le template
    donneurs_dict = {d.id: d for d in donneurs}

    return render_template('admin_gestion_utilisateurs.html',
                           utilisateurs=utilisateurs,
                           donneurs=donneurs,           # pour le <select> du formulaire
                           donneurs_dict=donneurs_dict) # pour affichage dans le tableau

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

    if not user.do_id:
        flash("Aucun Donneur d'Ordre n'est affecté à votre compte.", 'error')
        return redirect(url_for('login'))

    do = DonneurdOrdre.query.get(user.do_id)

    # === FILTRAGE FORT SUR TOUTES LES TABLES ===
    powerbi_base = "https://app.powerbi.com/reportEmbed?reportId=49c058b3-614b-48ec-a925-aa7b067a00a8&autoAuth=true&ctid=604f1a96-cbe8-43f8-abbf-f8eaf5d85730"
    
    filter_str = (
        f"DonneurdOrdre/id eq {user.do_id} and "
        f"Campagne/do_id eq {user.do_id} and "
        f"CDR/do_id eq {user.do_id}"
    )

    powerbi_url = f"{powerbi_base}&filter={filter_str}&ts={int(datetime.now().timestamp())}"

    return render_template('responsable_dashboard.html', 
                           user=user, 
                           do=do, 
                           powerbi_url=powerbi_url)


@app.route('/manager-dashboard')
@jwt_required()
def manager_dashboard():
    current_user_id = int(get_jwt_identity())
    user = Utilisateur.query.get_or_404(current_user_id)

    if user.role != 'manager':
        flash('Accès réservé aux managers', 'error')
        return redirect(url_for('login'))

    if not user.do_id:
        flash("Aucun Donneur d'Ordre n'est affecté à votre compte.", 'error')
        return redirect(url_for('login'))

    do = DonneurdOrdre.query.get(user.do_id)

    powerbi_base = "https://app.powerbi.com/reportEmbed?reportId=49c058b3-614b-48ec-a925-aa7b067a00a8&autoAuth=true&ctid=604f1a96-cbe8-43f8-abbf-f8eaf5d85730"
    
    filter_str = (
        f"DonneurdOrdre/id eq {user.do_id} and "
        f"Campagne/do_id eq {user.do_id} and "
        f"CDR/do_id eq {user.do_id}"
    )

    powerbi_url = f"{powerbi_base}&filter={filter_str}&ts={int(datetime.now().timestamp())}"

    return render_template('manager_dashboard.html', 
                           user=user, 
                           do=do, 
                           powerbi_url=powerbi_url)
# Création de comptes par le responsable plateau (seulement pour des managers)
@app.route('/responsable/creation-comptes', methods=['GET', 'POST'])
@jwt_required()
def responsable_creation_comptes():
    current_user_id = get_jwt_identity()
    current_user = Utilisateur.query.get(int(current_user_id))
    
    # Vérification : seulement responsable plateau
    if not current_user or current_user.role != 'responsable_plateau':
        flash('Accès réservé aux responsables plateau', 'error')
        return redirect(url_for('responsable_dashboard'))

    if request.method == 'POST':
        nom = request.form.get('nom')
        prenom = request.form.get('prenom')
        numtel = request.form.get('numtel')
        adresse = request.form.get('adresse')
        email = request.form.get('email')
        mot_de_passe = request.form.get('mot_de_passe')

        if not all([nom, prenom, numtel, adresse, email, mot_de_passe]):
            flash('Tous les champs sont obligatoires', 'error')
            return redirect(url_for('responsable_creation_comptes'))

        if Utilisateur.query.filter_by(email=email).first():
            flash('Cet email est déjà utilisé', 'error')
            return redirect(url_for('responsable_creation_comptes'))

        hashed = generate_password_hash(mot_de_passe)
        nouvel_utilisateur = Utilisateur(
            nom=nom,
            prenom=prenom,
            numtel=numtel,
            adresse=adresse,
            email=email,
            mot_de_passe=hashed,
            role='manager'  # FORCÉ : toujours manager
        )
        db.session.add(nouvel_utilisateur)
        db.session.commit()

        # Envoi email de bienvenue
        try:
            send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
                to=[{"email": email, "name": f"{prenom} {nom}"}],
                sender={"name": "Plateforme PFE", "email": "youssefbensaid839@gmail.com"},
                subject="Bienvenue sur la plateforme - Votre compte Manager",
                html_content=f"""
                <html>
                    <body style="font-family: Arial, sans-serif; color: #333;">
                        <h2 style="color: #f97316;">Bonjour {prenom} !</h2>
                        <p>Votre compte **Manager** a été créé par un responsable plateau.</p>
                        
                        <h3>Vos identifiants de connexion :</h3>
                        <ul style="line-height: 1.8;">
                            <li><strong>Email :</strong> {email}</li>
                            <li><strong>Mot de passe :</strong> {mot_de_passe}</li>
                            <li><strong>Rôle :</strong> Manager</li>
                        </ul>
                        
                        <p style="margin: 25px 0;">
                            <a href="{url_for('login', _external=True)}" 
                               style="background-color: #f97316; color: white; padding: 14px 28px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block;">
                                Se connecter maintenant
                            </a>
                        </p>
                        
                        <p style="font-size: 0.95em; color: #555; margin-top: 30px;">
                            Pour des raisons de sécurité, changez votre mot de passe dès votre première connexion.<br>
                            Si vous n'êtes pas à l'origine de ce compte, contactez immédiatement votre responsable.<br><br>
                            Cordialement,<br>
                            L'équipe de la plateforme
                        </p>
                    </body>
                </html>
                """
            )
            api_instance.send_transac_email(send_smtp_email)
            flash('Compte Manager créé avec succès ! Email de bienvenue envoyé.', 'success')
        except Exception as e:
            print("Erreur envoi email bienvenue :", e)
            flash('Compte créé mais l\'email de bienvenue n\'a pas pu être envoyé.', 'warning')

        return redirect(url_for('responsable_dashboard'))

    return render_template('responsable_creation_comptes.html')

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
            log_action(current_user_id, "CREATE", "DonneurdOrdre", donneur.id, new_value=donneur.nom)
            db.session.add(donneur)
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

            # Sauvegarder les anciennes données pour l'historique
            old_value = f"{donneur.nom} | {donneur.adresse} | {donneur.email} | {donneur.telephone}"

            # Log AVANT suppression
            log_action(
                current_user_id,
                "DELETE",
                "DonneurdOrdre",
                entite_id=donneur.id,
                old_value=old_value
            )

            db.session.delete(donneur)
            db.session.commit()

            flash('Donneur d\'Ordre supprimé avec succès', 'success')

        return redirect(url_for('gestion_donneurs_ordre'))
    # GET request
    donneurs = DonneurdOrdre.query.all()
    return render_template('admin_gestion_donneurs_ordre.html', donneurs=donneurs)



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
    return render_template('admin_gestion_campagnes.html', campagnes=campagnes, donneurs=donneurs)


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
            npv = NPV(
                numero=request.form.get('numero'),
                statut=request.form.get('statut'),
                campagne_id=request.form.get('campagne_id')
            )
            
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
    return render_template('admin_gestion_npv.html', npvs=npvs, campagnes=campagnes)

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
                log_action(current_user_id, "DELETE", "Operateur", operateur.id, operateur.nom)
                db.session.delete(operateur)
                db.session.commit()
                flash('Operateur supprimée avec succès', 'success')

        return redirect(url_for('gestion_operateurs'))

    operateurs = Operateur.query.all()
    return render_template('admin_gestion_operateurs.html', operateurs=operateurs)

# ===================== HISTORIQUE GLOBAL =====================
@app.route('/admin/historique')
@jwt_required()
def historique():
    current_user_id = int(get_jwt_identity())
    current_user = Utilisateur.query.get(current_user_id)

    if not current_user or current_user.email != 'youssefbensaid839@gmail.com':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    # Récupération + jointure avec l'utilisateur pour afficher le nom
    historiques = Historique.query\
        .options(joinedload(Historique.user))\
        .order_by(Historique.date_action.desc())\
        .all()

    return render_template('admin_historique.html', historiques=historiques)

if __name__ == '__main__':
    app.run(debug=True)