import pyodbc

# Pour LocalDB, le nom du serveur est généralement (localdb)\MSSQLLocalDB
# Ou (localdb)\v11.0, (localdb)\ProjectsV13, etc. – vérifie dans SSMS ou sqlcmd -L

conn_str = (
    r'DRIVER={ODBC Driver 18 for SQL Server};'
    r'SERVER=(localdb)\MSSQLLocalDB;'          # ← adapte si différent
    r'DATABASE=master;'                        # ou le nom de ta base
    r'Trusted_Connection=yes;'
    r'TrustServerCertificate=yes;'             # ← LA LIGNE QUI RÈGLE LE PROBLÈME
    # r'Encrypt=no;'                           # Option : tu peux aussi désactiver le chiffrement (moins sécurisé)
)

try:
    conn = pyodbc.connect(conn_str)
    print("Connexion réussie à LocalDB !")
    conn.close()
except Exception as e:
    print("Erreur :", e)