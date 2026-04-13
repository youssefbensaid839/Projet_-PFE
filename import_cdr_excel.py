import pandas as pd
from sqlalchemy import create_engine

excel_path = r"C:\Users\youss\Desktop\CDR.xlsx"
sheet_name = "Calls_Mezzo_2026-01-14_2026-01-"

conn_str = (
    r"mssql+pyodbc://@"
    r"(localdb)\MSSQLLocalDB"
    r"/TestCRUD"
    r"?driver=ODBC+Driver+18+for+SQL+Server"
    r"&Trusted_Connection=yes"
    r"&TrustServerCertificate=yes"
)

engine = create_engine(conn_str)

print("Lecture Excel...")
df = pd.read_excel(excel_path, sheet_name=sheet_name, engine='openpyxl')

print(f"Lignes lues : {len(df)}")
print("Colonnes :", df.columns.tolist())

# Conversion des colonnes numériques seulement
numeric_cols = [
    'Prix', "Coût de l'appel", 'tarif AMD', 'Surcharge CRM', 'Surcharge',
    'Recording fee', 'Coût HLM ', 'Prix STT', 'Coût STT', 'Coût de la réduction du bruit',
    'conv_agent_rate'
]

for col in numeric_cols:
    if col in df.columns:
        df[col] = pd.to_numeric(
            df[col].astype(str).replace({',': '.', ' ': '', '€': ''}, regex=True),
            errors='coerce'
        )

print("Import...")
try:
    df.to_sql('CDR', con=engine, schema='dbo', if_exists='append', index=False, chunksize=500)
    print("Succès !")
    print(f"Lignes insérées : {len(df)}")
except Exception as e:
    print("Erreur :", e)

print("\nVérifie dans SSMS :")
print("SELECT COUNT(*) FROM CDR;")
print("SELECT TOP 10 * FROM CDR ORDER BY Id DESC;")