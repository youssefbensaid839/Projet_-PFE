"""
Script de transfert CDR → NPV + Operateur
==========================================
CDR.[CLI envoyé]     → NPV.numero
CDR.[Campagne]       → NPV.campagne_id (via Campagne.nom)
CDR.[Destination]    → Operateur.nom
Operateur.pays       → 'France' par défaut
==========================================
"""

from app import app, db, CDR, Campagne, NPV, Operateur, DonneurdOrdre
from sqlalchemy import text
import pyodbc

def main():
    print("=" * 60)
    print("  TRANSFERT CDR → NPV + Opérateurs")
    print("=" * 60)

    with app.app_context():
        
        # ═══════════════════════════════════════════════════
        # ÉTAPE 0 : Connexion directe pour les requêtes SQL
        # ═══════════════════════════════════════════════════
        
        # Récupérer la connexion brute
        connection = db.engine.raw_connection()
        cursor = connection.cursor()
        
        try:
            # ═══════════════════════════════════════════════
            # ÉTAPE 1 : Extraire les CLI envoyés distincts
            # ═══════════════════════════════════════════════
            print("\n📥 Extraction des CLI envoyés distincts...")
            cursor.execute('SELECT DISTINCT [CLI envoyé] FROM CDR WHERE [CLI envoyé] IS NOT NULL')
            clis = [str(row[0]).strip() for row in cursor.fetchall() if row[0]]
            print(f"   {len(clis)} numéros distincts")
            
            # ═══════════════════════════════════════════════
            # ÉTAPE 2 : Extraire les Destinations distinctes
            # ═══════════════════════════════════════════════
            print("\n📥 Extraction des Destinations distinctes...")
            cursor.execute('SELECT DISTINCT [Destination] FROM CDR WHERE [Destination] IS NOT NULL')
            destinations = [str(row[0]).strip() for row in cursor.fetchall() if row[0]]
            print(f"   {len(destinations)} destinations distinctes")
            for d in sorted(destinations)[:10]:
                print(f"   '{d}'")
            
            # ═══════════════════════════════════════════════
            # ÉTAPE 3 : Extraire les associations
            # ═══════════════════════════════════════════════
            print("\n📥 Extraction des associations CLI-Campagne-Destination...")
            cursor.execute('''
                SELECT DISTINCT [CLI envoyé], [Campagne], [Destination]
                FROM CDR
                WHERE [CLI envoyé] IS NOT NULL
            ''')
            associations = []
            for row in cursor.fetchall():
                cli = str(row[0]).strip() if row[0] else None
                campagne = str(row[1]).strip() if row[1] else None
                dest = str(row[2]).strip() if row[2] else None
                if cli:
                    associations.append({
                        'npv': cli,
                        'campagne': campagne,
                        'dest': dest
                    })
            print(f"   {len(associations)} associations trouvées")
            
            # Afficher quelques exemples
            print("\n🔍 5 premiers exemples :")
            for a in associations[:5]:
                print(f"   CLI: {a['npv']} | Campagne: {a['campagne']} | Destination: {a['dest']}")
            
        finally:
            cursor.close()
            connection.close()
        
        # ═══════════════════════════════════════════════════
        # ÉTAPE 4 : Créer/Mettre à jour les Opérateurs
        # ═══════════════════════════════════════════════════
        print("\n📥 Import des Opérateurs...")
        
        operateur_map = {}
        stats_operateurs = {"crees": 0, "existants": 0}
        
        for dest in destinations:
            if not dest or dest.lower() == 'none':
                continue
            
            existant = Operateur.query.filter_by(nom=dest).first()
            if existant:
                operateur_map[dest] = existant
                stats_operateurs["existants"] += 1
            else:
                nouveau = Operateur(nom=dest, pays='France')
                db.session.add(nouveau)
                db.session.flush()
                operateur_map[dest] = nouveau
                stats_operateurs["crees"] += 1
                print(f"   🆕 Opérateur : '{dest}'")
        
        db.session.commit()
        print(f"   ✅ {stats_operateurs['crees']} créés, {stats_operateurs['existants']} déjà existants")
        
        # ═══════════════════════════════════════════════════
        # ÉTAPE 5 : Charger les Campagnes existantes
        # ═══════════════════════════════════════════════════
        print("\n📋 Chargement des Campagnes...")
        
        campagne_map = {}
        for c in Campagne.query.all():
            campagne_map[c.nom.strip()] = c
            do_nom = c.do.nom if c.do else '—'
            print(f"   '{c.nom}' → DO: {do_nom} (do_id={c.do_id})")
        
        print(f"   ✅ {len(campagne_map)} campagnes chargées")
        
        # ═══════════════════════════════════════════════════
        # ÉTAPE 6 : Créer les NPV
        # ═══════════════════════════════════════════════════
        print("\n📥 Création des NPV...")
        
        stats_npv = {"crees": 0, "existants": 0, "sans_campagne": 0}
        
        for i, assoc in enumerate(associations):
            npv_numero = assoc['npv']
            campagne_nom = assoc['campagne']
            dest = assoc['dest']
            
            # Vérifier doublon
            existant = NPV.query.filter_by(numero=npv_numero).first()
            if existant:
                stats_npv["existants"] += 1
                
                # Mettre à jour operateur_id si NULL
                if existant.operateur_id is None and dest and dest in operateur_map:
                    existant.operateur_id = operateur_map[dest].id
                
                continue
            
            # Trouver campagne_id et do_id
            campagne_id = None
            do_id = None
            if campagne_nom and campagne_nom in campagne_map:
                campagne_id = campagne_map[campagne_nom].id
                do_id = campagne_map[campagne_nom].do_id
            elif campagne_nom:
                stats_npv["sans_campagne"] += 1
                if stats_npv["sans_campagne"] <= 5:
                    print(f"   ⚠ Campagne '{campagne_nom}' non trouvée")
            
            # Trouver operateur_id
            operateur_id = None
            if dest and dest in operateur_map:
                operateur_id = operateur_map[dest].id
            
            # Créer
            npv = NPV(
                numero=npv_numero,
                statut=None,
                campagne_id=campagne_id,
                do_id=do_id,
                operateur_id=operateur_id
            )
            db.session.add(npv)
            stats_npv["crees"] += 1
            
            if stats_npv["crees"] <= 10:
                print(f"   🆕 {npv_numero} | Campagne: {campagne_nom or '—'} | DO: {do_id or '—'} | Op: {dest or '—'}")
            
            # Commit par lots de 500
            if stats_npv["crees"] % 500 == 0:
                db.session.commit()
                print(f"   ... {stats_npv['crees']} NPV créés...")
        
        db.session.commit()
        
        # ═══════════════════════════════════════════════════
        # RÉSULTAT
        # ═══════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("  📊 RÉSULTAT")
        print("=" * 60)
        print(f"  Opérateurs créés     : {stats_operateurs['crees']}")
        print(f"  Opérateurs existants : {stats_operateurs['existants']}")
        print(f"  NPV créés           : {stats_npv['crees']}")
        print(f"  NPV déjà existants  : {stats_npv['existants']}")
        print(f"  NPV sans campagne   : {stats_npv['sans_campagne']}")
        print(f"  Total NPV en base   : {NPV.query.count()}")
        print("=" * 60)
        print("✅ Transfert terminé !")


if __name__ == "__main__":
    main()