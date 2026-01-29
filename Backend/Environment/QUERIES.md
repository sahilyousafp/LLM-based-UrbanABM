# Spatial SQL using DuckDB

## Connecting
You can connect to the database using Python, the DuckDB CLI, or DBeaver.

**Python:**
```python
import duckdb
con = duckdb.connect('Eixample_OSM.duckdb')
con.install_extension('spatial')
con.load_extension('spatial')
```

**CLI:**
```bash
duckdb Eixample_OSM.duckdb
INSTALL spatial;
LOAD spatial;
```

## Example Queries

### 1. Simple Count
Count the number of cafes.
```sql
SELECT COUNT(*) 
FROM amenities 
WHERE amenity = 'cafe';
```

### 2. Spatial Intersection (Point in Polygon)
Find which buildings contain a specific shop type.
```sql
SELECT b.building, a.name, a.amenity
FROM buildings b, amenities a
WHERE ST_Intersects(b.geometry, a.geometry)
AND a.amenity = 'supermarket';
```

### 3. Proximity Search (Distance)
Find all bus stops within 50 meters of a specific location (using a sample point).
*Note: ST_Distance returns degrees for Lat/Lon. For meters, it is best to project to a local CRS (like UTM) or use `ST_Distance_Spheroid` if available, or rough approximations.*
```sql
-- Using ST_DWithin (assuming projected coordinates or handling degrees)
SELECT name, ST_AsText(geometry)
FROM amenities
WHERE ST_DWithin(geometry, ST_Point(2.17, 41.39), 0.005); -- ~500m
```

### 4. Network Lookup
Find edges connected to a specific node.
```sql
SELECT * 
FROM walk_edges 
WHERE u = 123456789;
```
