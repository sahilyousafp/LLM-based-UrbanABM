from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import duckdb
from shapely import wkt

app = FastAPI()

DB_PATH = r"d:\IaaC\2ND YEAR\THESIS\CODE EXPLORATIONS\Environment\Term02\DuckDB_OSM_Pipeline\eixample_osm.duckdb"

@app.get("/", response_class=HTMLResponse)
async def debug_page():
    """Debug page showing database contents"""
    
    con = duckdb.connect(DB_PATH, read_only=True)
    con.install_extension("spatial")
    con.load_extension("spatial")
    
    html = "<html><head><title>Database Debug</title></head><body>"
    html += "<h1>DuckDB Database Contents</h1>"
    
    # List all tables
    html += "<h2>Tables:</h2><ul>"
    tables = con.execute("SHOW TABLES").fetchall()
    table_info = {}
    for t in tables:
        table_name = t[0]
        count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        table_info[table_name] = count
        html += f"<li><b>{table_name}</b>: {count:,} rows</li>"
    html += "</ul>"
    
    # Check walk_edges specifically
    html += "<h2>Walk Edges Details:</h2>"
    if 'walk_edges' in table_info:
        count = table_info['walk_edges']
        html += f"<p>Total walk_edges: {count:,}</p>"
        
        if count > 0:
            # Get first few
            results = con.execute("SELECT ST_AsText(geometry) as wkt FROM walk_edges LIMIT 5").fetchall()
            html += "<h3>First 5 walk edges:</h3><ol>"
            for idx, row in enumerate(results):
                try:
                    geom = wkt.loads(row[0])
                    coords = list(geom.coords)
                    html += f"<li>Edge {idx+1}: {len(coords)} points<br>"
                    html += f"Start: ({coords[0][0]:.6f}, {coords[0][1]:.6f})<br>"
                    html += f"End: ({coords[-1][0]:.6f}, {coords[-1][1]:.6f})</li>"
                except Exception as e:
                    html += f"<li>Error: {e}</li>"
            html += "</ol>"
            
            # Check coordinate ranges
            bbox = con.execute("""
                SELECT 
                    MIN(ST_XMin(geometry)) as minx,
                    MAX(ST_XMax(geometry)) as maxx,
                    MIN(ST_YMin(geometry)) as miny,
                    MAX(ST_YMax(geometry)) as maxy
                FROM walk_edges
            """).fetchone()
            
            html += "<h3>Coordinate Bounds:</h3>"
            html += f"<p>Longitude: {bbox[0]:.6f} to {bbox[1]:.6f}</p>"
            html += f"<p>Latitude: {bbox[2]:.6f} to {bbox[3]:.6f}</p>"
            
            # Check if coordinates look like Barcelona
            if 2.0 < bbox[0] < 2.3 and 41.3 < bbox[2] < 41.5:
                html += "<p style='color:green'>✓ Coordinates look correct for Barcelona (WGS84)</p>"
            else:
                html += "<p style='color:red'>✗ Coordinates don't look like Barcelona. Might be in wrong CRS.</p>"
        else:
            html += "<p style='color:red'>walk_edges table is EMPTY!</p>"
    else:
        html += "<p style='color:red'>walk_edges table does NOT exist!</p>"
        html += "<p>Available tables: " + ", ".join(table_info.keys()) + "</p>"
    
    # Check buildings for comparison
    html += "<h2>Buildings (for comparison):</h2>"
    if 'buildings' in table_info and table_info['buildings'] > 0:
        bbox = con.execute("""
            SELECT 
                MIN(ST_XMin(geometry)) as minx,
                MAX(ST_XMax(geometry)) as maxx,
                MIN(ST_YMin(geometry)) as miny,
                MAX(ST_YMax(geometry)) as maxy
            FROM buildings
        """).fetchone()
        html += f"<p>Buildings bounds: Lon {bbox[0]:.6f} to {bbox[1]:.6f}, Lat {bbox[2]:.6f} to {bbox[3]:.6f}</p>"
    
    con.close()
    
    html += "</body></html>"
    return html

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("debug_server:app", host="127.0.0.1", port=8001, reload=True)
