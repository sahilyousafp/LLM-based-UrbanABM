-- Sample spatial queries for database benchmark
-- Run on DuckDB, SQLite+SpatiaLite, and PostgreSQL+PostGIS

-- Q1: Single agent nearest-amenity lookup (radius ~50m)
SELECT name, amenity,
       ST_Distance(geometry, ST_GeomFromText('POINT (2.1686 41.3954)')) as dist_deg
FROM amenities
WHERE ST_DWithin(geometry, ST_GeomFromText('POINT (2.1686 41.3954)'), 0.001)
ORDER BY dist_deg
LIMIT 20;

-- Q2: 500-agent bulk nearest-amenity (simulating one simulation step)
-- (Replace with UNNEST/VALUES for actual bulk test in each DB)
SELECT a.name, a.amenity, ST_Distance(a.geometry, p.point) as dist
FROM amenities a,
     (VALUES
       (ST_GeomFromText('POINT (2.1686 41.3954)')),
       (ST_GeomFromText('POINT (2.1720 41.3970)')),
       (ST_GeomFromText('POINT (2.1650 41.3940)'))
     ) p(point)
WHERE ST_DWithin(a.geometry, p.point, 0.001)
ORDER BY dist
LIMIT 100;

-- Q3: Walk-edge endpoint lookup (node-based network traversal)
SELECT rowid as edge_id,
       ST_AsText(ST_StartPoint(geometry)) as start_wkt,
       ST_AsText(ST_EndPoint(geometry)) as end_wkt
FROM walk_edges
WHERE ST_DWithin(
    ST_StartPoint(geometry),
    ST_GeomFromText('POINT (2.1686 41.3954)'),
    0.0001
);

-- Q4: Amenity count by type
SELECT amenity, COUNT(*) as count
FROM amenities
GROUP BY amenity
ORDER BY count DESC;

-- Q5: Network coverage extent
SELECT
    MIN(ST_XMin(geometry)) as min_lon,
    MAX(ST_XMax(geometry)) as max_lon,
    MIN(ST_YMin(geometry)) as min_lat,
    MAX(ST_YMax(geometry)) as max_lat,
    COUNT(*) as edge_count
FROM walk_edges;
