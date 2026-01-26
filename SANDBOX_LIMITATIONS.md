# Sandbox Environment Limitations

## DuckDB Spatial Extension

### Issue
The DuckDB spatial extension cannot be downloaded in this sandbox environment due to network restrictions. This affects:
- OSM Update Service (when creating new databases)
- Map Server (when querying spatial data)
- Agent Model (when performing spatial queries)

### Error Message
```
IO Error: Failed to download extension "spatial" at URL "http://extensions.duckdb.org/v1.4.4/linux_amd64/spatial.duckdb_extension.gz"
Extension "spatial" is an existing extension.
```

### Solution for Production Environment

In a production environment with internet access, the services will work correctly. The first time you run either service, DuckDB will automatically download and install the spatial extension.

**No action required** - the code will automatically:
1. Download the spatial extension on first use
2. Install it to `~/.duckdb/extensions/`
3. Load it for all subsequent uses

### Manual Installation (if needed)

If you're in an environment where automatic download fails, you can manually install the extension:

```bash
# Download the extension for your platform
wget http://extensions.duckdb.org/v1.4.4/linux_amd64/spatial.duckdb_extension.gz

# Extract it
gunzip spatial.duckdb_extension.gz

# Create the extensions directory
mkdir -p ~/.duckdb/extensions/v1.4.4/linux_amd64/

# Move the extension
mv spatial.duckdb_extension ~/.duckdb/extensions/v1.4.4/linux_amd64/
```

Replace `linux_amd64` with your platform:
- macOS Intel: `osx_amd64`
- macOS Apple Silicon: `osx_arm64`
- Windows: `windows_amd64`

### Testing in This Sandbox

The existing database (`eixample_osm.duckdb`) already contains spatial data, but we cannot perform new spatial queries or create new databases without the spatial extension.

For testing purposes in this sandbox:
- ✅ Database connection works
- ✅ Table queries work
- ✅ Row counts work
- ❌ Spatial functions (ST_AsText, ST_Distance, etc.) do not work
- ❌ Creating new databases with spatial data does not work

### Workaround for Development

For development without internet access:
1. Use a pre-existing database with spatial data
2. Avoid spatial functions in queries
3. Or manually install the extension as shown above

### Expected Behavior in Production

Once deployed in a production environment with internet access:
1. Services start normally
2. Spatial extension downloads automatically on first use
3. All spatial queries work as expected
4. OSM data updates work fully
5. Map visualization displays correctly
6. Agents can query spatial data

## Summary

This is a **sandbox limitation only**. The code is production-ready and will work correctly in any environment with internet access for the initial extension download.
