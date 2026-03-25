import pyodbc

print("Pilotes ODBC installés :")
for driver in pyodbc.drivers():
    print(f" - {driver}")