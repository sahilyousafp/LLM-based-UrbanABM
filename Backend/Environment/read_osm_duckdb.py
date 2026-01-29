import duckdb
import pandas as pd
import os
from tabulate import tabulate

def read_duckdb_osm(db_path):
    """
    Connects to the DuckDB OSM database and displays information about each table
    using a bordered table format.
    """
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at {db_path}")
        return

    print(f"\n{'='*60}")
    print(f" Connecting to OSM Database: {os.path.basename(db_path)}")
    print(f"{'='*60}\n")
    
    # Connect in read-only mode to avoid locking issues
    conn = duckdb.connect(db_path, read_only=True)
    
    try:
        # Get list of tables
        tables = conn.execute("SHOW TABLES").fetchall()
        
        if not tables:
            print("No tables found in the database.")
            return

        for table_tuple in tables:
            table_name = table_tuple[0]
            
            # Get record count
            count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            
            print(f" TABLE: {table_name.upper()}")
            print(f" Total Records: {count}")
            
            # Read first 5 rows into a Pandas DataFrame
            df = conn.execute(f"SELECT * FROM {table_name} LIMIT 5").fetchdf()
            
            if not df.empty:
                # Truncate long strings (especially geometry) for terminal display
                for col in df.columns:
                    if df[col].dtype == 'object':
                        df[col] = df[col].apply(lambda x: str(x)[:30] + '...' if len(str(x)) > 30 else x)
                
                # Display using tabulate with borders
                print(tabulate(df, headers='keys', tablefmt='grid', showindex=False))
            else:
                print(" [Table is empty]")
            print("\n")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    db_file = r"d:\IaaC\2ND YEAR\THESIS\CODE EXPLORATIONS\Environment\Term02\DuckDB_OSM_Pipeline\eixample_osm.duckdb"
    read_duckdb_osm(db_file)
