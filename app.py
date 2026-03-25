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
class Historique(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('utilisateurs.id'), nullable=False)
    
    action = db.Column(db.String(50), nullable=False)      # CREATE, UPDATE, DELETE
    entite = db.Column(db.String(50), nullable=False)
    entite_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.String(500), nullable=True)
    date_action = db.Column(db.DateTime, default=datetime.utcnow)

    # Relation correcte
    user = db.relationship('Utilisateur', backref=db.backref('historiques', lazy=True))

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
    """Enregistre l'action avec comparaison claire Ancienne → Nouvelle"""
    try:
        if action.upper() == "UPDATE" and old_value is not None and new_value is not None:
            details = f"{old_value} → {new_value}"
        elif action.upper() == "CREATE":
            details = f"Création : {new_value}"
        elif action.upper() == "DELETE":
            details = f"Suppression : {old_value or new_value}"
        else:
            details = new_value or ""

        log = Historique(
            user_id=current_user_id,
            action=action.upper(),
            entite=entite,
            entite_id=entite_id,
            details=details
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[HISTORIQUE ERREUR] {str(e)}")
# Gestion des utilisateurs (accessible depuis admin)
@app.route('/admin/gestion-utilisateurs', methods=['GET', 'POST'])
@jwt_required()
def gestion_utilisateurs():
    current_user_id = get_jwt_identity()
    current_user = Utilisateur.query.get(int(current_user_id))
    
    # Vérification admin (tu peux garder ton email ou passer à role == 'admin')
    if not current_user or current_user.email != 'youssefbensaid839@gmail.com':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')
        user_id = request.form.get('user_id')

        if action == 'create':
            nom = request.form.get('nom')
            prenom = request.form.get('prenom')
            numtel = request.form.get('numtel')
            adresse = request.form.get('adresse')
            email = request.form.get('email')
            mot_de_passe = request.form.get('mot_de_passe')
            role = request.form.get('role')

            if not all([nom, prenom, numtel, adresse, email, mot_de_passe, role]):
                flash('Tous les champs sont obligatoires', 'error')
                return redirect(url_for('gestion_utilisateurs'))

            if role not in ['admin', 'responsable_plateau', 'manager', 'user']:
                flash('Rôle invalide', 'error')
                return redirect(url_for('gestion_utilisateurs'))

            if Utilisateur.query.filter_by(email=email).first():
                flash('Cet email est déjà utilisé', 'error')
                return redirect(url_for('gestion_utilisateurs'))

            hashed = generate_password_hash(mot_de_passe)
            nouvel_utilisateur = Utilisateur(
                nom=nom,
                prenom=prenom,
                numtel=numtel,
                adresse=adresse,
                email=email,
                mot_de_passe=hashed,
                role=role
            )
            db.session.add(nouvel_utilisateur)
            db.session.commit()

            # Envoi email de bienvenue
            try:
                send_smtp_email = SendSmtpEmail(
                    to=[{"email": email, "name": f"{prenom} {nom}"}],
                    sender={"name": "Plateforme PFE", "email": "youssefbensaid839@gmail.com"},
                    subject="Bienvenue sur la plateforme - Vos identifiants",
                    html_content=f"""
                    <html>
                        <body style="font-family: Arial, sans-serif; color: #333;">
                            <h2 style="color: #f97316;">Bienvenue {prenom} !</h2>
                            <p>Votre compte a été créé par l'administrateur.</p>
                            
                            <h3>Vos identifiants :</h3>
                            <ul style="line-height: 1.8;">
                                <li><strong>Email :</strong> {email}</li>
                                <li><strong>Mot de passe :</strong> {mot_de_passe}</li>
                                <li><strong>Rôle :</strong> {role}</li>
                            </ul>
                            
                            <p style="margin: 20px 0;">
                                <a href="{url_for('login', _external=True)}" 
                                   style="background-color: #f97316; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block;">
                                    Se connecter maintenant
                                </a>
                            </p>
                            
                            <p style="font-size: 0.9em; color: #666;">
                                Changez votre mot de passe dès votre première connexion.<br>
                                Cordialement,<br>
                                L'équipe de la plateforme
                            </p>
                        </body>
                    </html>
                    """
                )
                api_instance.send_transac_email(send_smtp_email)
                flash('Utilisateur créé et email envoyé', 'success')
            except ApiException as e:
                flash(f'Utilisateur créé mais erreur email : {str(e)}', 'warning')

        elif action == 'update':
            user = Utilisateur.query.get(user_id)
            if user:
                ancien_role = user.role

                user.nom = request.form.get('nom', user.nom)
                user.prenom = request.form.get('prenom', user.prenom)
                user.numtel = request.form.get('numtel', user.numtel)
                user.adresse = request.form.get('adresse', user.adresse)
                user.email = request.form.get('email', user.email)
                user.role = request.form.get('role', user.role)

                db.session.commit()

                # Envoi email de confirmation si rôle changé
                if ancien_role != user.role:
                    try:
                        send_smtp_email = SendSmtpEmail(
                            to=[{"email": user.email, "name": f"{user.prenom} {user.nom}"}],
                            sender={"name": "Plateforme PFE", "email": "youssefbensaid839@gmail.com"},
                            subject="Modification de votre rôle sur la plateforme",
                            html_content=f"""
                            <html>
                                <body style="font-family: Arial, sans-serif; color: #333;">
                                    <h2 style="color: #f97316;">Bonjour {user.prenom},</h2>
                                    <p>Votre rôle a été modifié par l'administrateur.</p>
                                    <h3>Nouveau rôle :</h3>
                                    <p style="font-size: 1.2em; font-weight: bold;">{user.role}</p>
                                    <p>Vous pouvez désormais accéder aux fonctionnalités correspondantes.</p>
                                    <p style="margin: 20px 0;">
                                        <a href="{url_for('login', _external=True)}" 
                                           style="background-color: #f97316; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block;">
                                            Se connecter
                                        </a>
                                    </p>
                                    <p style="font-size: 0.9em; color: #666;">
                                        Cordialement,<br>
                                        L'équipe de la plateforme
                                    </p>
                                </body>
                            </html>
                            """
                        )
                        api_instance.send_transac_email(send_smtp_email)
                        flash('Utilisateur modifié et email de confirmation envoyé', 'success')
                    except ApiException as e:
                        flash('Utilisateur modifié mais erreur envoi email', 'warning')
                else:
                    flash('Utilisateur modifié avec succès', 'success')
            else:
                flash('Utilisateur introuvable', 'error')

        elif action == 'delete':
            user = Utilisateur.query.get(user_id)
            if user:
                db.session.delete(user)
                db.session.commit()
                flash('Utilisateur supprimé avec succès', 'success')
            else:
                flash('Utilisateur introuvable', 'error')

        return redirect(url_for('gestion_utilisateurs'))

    utilisateurs = Utilisateur.query.all()
    return render_template('admin_gestion_utilisateurs.html', utilisateurs=utilisateurs)

# Modifier un utilisateur existant
@app.route('/admin/utilisateur/<int:user_id>/modifier', methods=['GET', 'POST'])
@jwt_required()
def modifier_utilisateur(user_id):
    current_user_id = get_jwt_identity()
    current_user = Utilisateur.query.get(int(current_user_id))
    if not current_user or current_user.email != 'youssefbensaid839@gmail.com':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    utilisateur = Utilisateur.query.get_or_404(user_id)

    if request.method == 'POST':
        nom = request.form.get('nom')
        prenom = request.form.get('prenom')
        numtel = request.form.get('numtel')
        adresse = request.form.get('adresse')
        email = request.form.get('email')
        mot_de_passe = request.form.get('mot_de_passe')

        if not all([nom, prenom, numtel, adresse, email]):
            flash('Tous les champs obligatoires doivent être remplis', 'error')
            return redirect(url_for('modifier_utilisateur', user_id=user_id))

        # Vérifier si l'email change et s'il est déjà pris
        if email != utilisateur.email and Utilisateur.query.filter_by(email=email).first():
            flash('Cet email est déjà utilisé par un autre utilisateur', 'error')
            return redirect(url_for('modifier_utilisateur', user_id=user_id))

        utilisateur.nom = nom
        utilisateur.prenom = prenom
        utilisateur.numtel = numtel
        utilisateur.adresse = adresse
        utilisateur.email = email

        # Si mot de passe saisi, on le met à jour
        if mot_de_passe and mot_de_passe.strip():
            utilisateur.mot_de_passe = generate_password_hash(mot_de_passe)

        db.session.commit()
        flash('Utilisateur modifié avec succès !', 'success')
        return redirect(url_for('gestion_utilisateurs'))

    return render_template('admin_modifier_utilisateur.html', utilisateur=utilisateur)


# Supprimer un utilisateur
@app.route('/admin/utilisateur/<int:user_id>/supprimer', methods=['POST'])
@jwt_required()
def supprimer_utilisateur(user_id):
    current_user_id = get_jwt_identity()
    current_user = Utilisateur.query.get(int(current_user_id))
    if not current_user or current_user.email != 'youssefbensaid839@gmail.com':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    utilisateur = Utilisateur.query.get_or_404(user_id)

    # On empêche de supprimer l'admin par défaut (par sécurité)
    if utilisateur.email == 'youssefbensaid839@gmail.com':
        flash('Impossible de supprimer le compte administrateur principal', 'error')
        return redirect(url_for('gestion_utilisateurs'))

    db.session.delete(utilisateur)
    db.session.commit()

    flash('Utilisateur supprimé avec succès', 'success')
    return redirect(url_for('gestion_utilisateurs'))
# Dashboard Responsable Plateau (vide pour l'instant)
@app.route('/responsable-dashboard')
@jwt_required()
def responsable_dashboard():
    current_user_id = get_jwt_identity()
    user = Utilisateur.query.get(int(current_user_id))
    if not user or user.role != 'responsable_plateau':
        flash('Accès réservé aux responsables plateau', 'error')
        return redirect(url_for('login'))
    return render_template('responsable_dashboard.html')

# Dashboard Manager (vide pour l'instant)
@app.route('/manager-dashboard')
@jwt_required()
def manager_dashboard():
    current_user_id = get_jwt_identity()
    user = Utilisateur.query.get(int(current_user_id))
    if not user or user.role != 'manager':
        flash('Accès réservé aux managers', 'error')
        return redirect(url_for('login'))
    return render_template('manager_dashboard.html')
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
@app.route('/admin/gestion-donneurs-ordre', methods=['GET', 'POST'])
@jwt_required()
def gestion_donneurs_ordre():
    current_user_id = int(get_jwt_identity())
    current_user = Utilisateur.query.get(current_user_id)
    if not current_user or current_user.role != 'admin':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')
        do_id = request.form.get('do_id')

        if action == 'create':
            do = DonneurdOrdre(
                nom=request.form.get('nom'),
                adresse=request.form.get('adresse'),
                telephone=request.form.get('telephone'),
                email=request.form.get('email')
            )
            db.session.add(do)
            db.session.commit()
            log_action(current_user_id, "CREATE", "DonneurdOrdre", do.id, None, do.nom)

        elif action == 'update':
            do = DonneurdOrdre.query.get(do_id)
            if do:
                # Sauvegarde de l'ancienne valeur complète
                old_value = f"{do.nom} | {do.adresse} | {do.telephone} | {do.email}"
                
                # Mise à jour des champs
                do.nom = request.form.get('nom', do.nom)
                do.adresse = request.form.get('adresse', do.adresse)
                do.telephone = request.form.get('telephone', do.telephone)
                do.email = request.form.get('email', do.email)
                
                db.session.commit()
                
                # Nouvelle valeur complète
                new_value = f"{do.nom} | {do.adresse} | {do.telephone} | {do.email}"
                
                log_action(current_user_id, "UPDATE", "DonneurdOrdre", do.id, old_value, new_value)

        elif action == 'delete':
            do = DonneurdOrdre.query.get(do_id)
            if do:
                deleted_info = f"{do.nom} | {do.adresse}"
                log_action(current_user_id, "DELETE", "DonneurdOrdre", do.id, deleted_info)
                db.session.delete(do)
                db.session.commit()

        return redirect(url_for('gestion_donneurs_ordre'))

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
            db.session.add(campagne)
            db.session.commit()
            log_action(current_user_id, "CREATE", "Campagne", campagne.id, None, campagne.nom)

        elif action == 'update':
            campagne = Campagne.query.get(campagne_id)
            if campagne:
                # Ancienne valeur complète
                old_value = f"{campagne.nom} | {campagne.date_debut} → {campagne.date_fin} | DO:{campagne.do_id}"
                
                campagne.nom = request.form.get('nom', campagne.nom)
                campagne.date_debut = request.form.get('date_debut', campagne.date_debut)
                campagne.date_fin = request.form.get('date_fin', campagne.date_fin)
                campagne.do_id = request.form.get('do_id', campagne.do_id)
                
                db.session.commit()
                
                # Nouvelle valeur complète
                new_value = f"{campagne.nom} | {campagne.date_debut} → {campagne.date_fin} | DO:{campagne.do_id}"
                
                log_action(current_user_id, "UPDATE", "Campagne", campagne.id, old_value, new_value)

        elif action == 'delete':
            campagne = Campagne.query.get(campagne_id)
            if campagne:
                deleted_info = f"{campagne.nom} (DO: {campagne.do_id})"
                log_action(current_user_id, "DELETE", "Campagne", campagne.id, deleted_info)
                db.session.delete(campagne)
                db.session.commit()

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
            db.session.add(npv)
            db.session.commit()
            log_action(current_user_id, "CREATE", "NPV", npv.id, None, npv.numero)

        elif action == 'update':
            npv = NPV.query.get(npv_id)
            if npv:
                old_value = f"{npv.numero} ({npv.statut}) | Campagne:{npv.campagne_id}"
                
                npv.numero = request.form.get('numero', npv.numero)
                npv.statut = request.form.get('statut', npv.statut)
                npv.campagne_id = request.form.get('campagne_id', npv.campagne_id)
                
                db.session.commit()
                
                new_value = f"{npv.numero} ({npv.statut}) | Campagne:{npv.campagne_id}"
                log_action(current_user_id, "UPDATE", "NPV", npv.id, old_value, new_value)

        elif action == 'delete':
            npv = NPV.query.get(npv_id)
            if npv:
                log_action(current_user_id, "DELETE", "NPV", npv.id, npv.numero)
                db.session.delete(npv)
                db.session.commit()

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
            db.session.add(operateur)
            db.session.commit()
            log_action(current_user_id, "CREATE", "Operateur", operateur.id, None, operateur.nom)

        elif action == 'update':
            operateur = Operateur.query.get(operateur_id)
            if operateur:
                old_value = f"{operateur.nom} ({operateur.code}) | {operateur.pays or '—'}"
                
                operateur.nom = request.form.get('nom', operateur.nom)
                operateur.code = request.form.get('code', operateur.code)
                operateur.pays = request.form.get('pays', operateur.pays)
                
                db.session.commit()
                
                new_value = f"{operateur.nom} ({operateur.code}) | {operateur.pays or '—'}"
                log_action(current_user_id, "UPDATE", "Operateur", operateur.id, old_value, new_value)

        elif action == 'delete':
            operateur = Operateur.query.get(operateur_id)
            if operateur:
                log_action(current_user_id, "DELETE", "Operateur", operateur.id, operateur.nom)
                db.session.delete(operateur)
                db.session.commit()

        return redirect(url_for('gestion_operateurs'))

    operateurs = Operateur.query.all()
    return render_template('admin_gestion_operateurs.html', operateurs=operateurs)
# ===================== HISTORIQUE GLOBAL =====================
@app.route('/admin/historique')
@jwt_required()
def historique():
    current_user_id = int(get_jwt_identity())
    current_user = Utilisateur.query.get(current_user_id)
    
    if not current_user or current_user.role != 'admin':
        flash('Accès réservé à l\'administrateur', 'error')
        return redirect(url_for('admin_dashboard'))

    # Récupère l'historique du plus récent au plus ancien
    historiques = Historique.query.order_by(Historique.date_action.desc()).all()

    return render_template('admin_historique.html', historiques=historiques)


if __name__ == '__main__':
    app.run(debug=True)
    
    
    
    #test