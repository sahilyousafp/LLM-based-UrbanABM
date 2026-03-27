# Configuration Guide - Urban ABM

## Overview

This guide covers all configuration options for the Urban ABM simulation, including the new **rule-based movement mode** that provides deterministic, LLM-free agent behavior.

## Environment File (`.env`)

**File:** `.env` (project root)

```env
# ============================================
# URBAN ABM - SYSTEM CONFIGURATION
# ============================================

# ── LLM Provider Configuration ────────────────────────────────────────
# Options: ollama (local), openai, gemini, vllm, deepseek
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash-lite
LLM_API_KEY=
LLM_BASE_URL=
LLM_TIMEOUT=30
LLM_MAX_TOKENS=256
LLM_TEMPERATURE=0.7

# ── API Keys ──────────────────────────────────────────────────────────
GEMINI_API_KEY=
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
MAPBOX_TOKEN=pk.your_mapbox_token_here

# ── Google Cloud Platform (for Overture Maps via BigQuery) ────────────
# Option 1: API Key (Simplest - for development/testing)
# Create at: https://console.cloud.google.com/apis/credentials
GOOGLE_API_KEY=your-google-cloud-api-key

# Option 2: Service Account JSON file path (recommended for production)
# Download from: https://console.cloud.google.com/iam-admin/serviceaccounts
GOOGLE_APPLICATION_CREDENTIALS=path/to/your/service-account-key.json

# Option 3: GCP Project ID (required for BigQuery access)
# Create at: https://console.cloud.google.com/projectcreate
GOOGLE_CLOUD_PROJECT=your-gcp-project-id

# Option 4: Application Default Credentials (for local development)
# Run once: gcloud auth application-default login
# (No .env entry needed - credentials stored in ~/.config/gcloud)

# ── Agent Configuration ───────────────────────────────────────────────
# Number of agents in the simulation (1-100 recommended)
NUM_AGENTS=50

# Spawn seed for reproducible agent placement (optional)
# Leave empty or comment out for random spawning
#SPAWN_SEED=42

# LLM calls per step (for hybrid mode - not used in rule_based mode)
# 0 = all rule-based, 50 = mix, 100+ = all LLM
LLM_CALLS_PER_STEP=50

# ── Perception Mode ───────────────────────────────────────────────────
# Options: amenities, perception, both, rule_based
# Default: both
PERCEPTION_MODE=both

# ── Database Configuration ────────────────────────────────────────────
# DuckDB database path (relative to Backend/Agent)
DATABASE_PATH=..\Environment\eixample_overture.duckdb

# ── Server Configuration ──────────────────────────────────────────────
HOST=127.0.0.1
PORT=8000
RELOAD=true
```

---

## Perception Modes

The system now supports **4 perception modes** that control how agents perceive and navigate the environment:

### 1. `both` (Default)
- **LLM-Driven:** Yes
- **Perception:** Amenities + Street View Perception Points
- **Use Case:** Full simulation with complete environmental awareness
- **Performance:** Highest LLM usage, richest behavior

### 2. `amenities`
- **LLM-Driven:** Yes
- **Perception:** Amenities only (shops, parks, transport, etc.)
- **Use Case:** Focus on destination-based movement without visual perception
- **Performance:** Moderate LLM usage

### 3. `perception`
- **LLM-Driven:** Yes
- **Perception:** Street view perception points only
- **Use Case:** Focus on visual/urban quality-driven movement
- **Performance:** Moderate LLM usage

### 4. `rule_based` ⭐ NEW
- **LLM-Driven:** **No**
- **Perception:** None (deterministic algorithm)
- **Movement Strategy:** Least-visited edge selection
- **Use Case:** 
  - Baseline comparison for LLM behavior
  - High-performance simulation (no LLM latency)
  - Reproducible deterministic movement
  - Large-scale agent testing
- **Performance:** **Fastest** (no LLM calls)

---

## How Perception Modes Work

### LLM-Driven Modes (`both`, `amenities`, `perception`)

```
Agent Step → Query Environment → LLM Decision → Move
                ↓
        (Amenities and/or
       Perception Points)
```

- Agents gather environmental data based on mode
- LLM processes information and generates decision
- Agents execute movement with fallback protection
- **LLM Fallback:** Enabled (hardcoded `llm_fallback = True`)

### Rule-Based Mode (`rule_based`)

```
Agent Step → Select Least-Visited Edge → Move
```

- No LLM calls
- Deterministic algorithm:
  1. Find connected edges from current position
  2. Filter out current edge (no U-turns)
  3. Sort by global visit count
  4. Select least-visited edge
  5. Move along edge at constant speed
- Fully reproducible with same seed

---

## Changing Perception Mode

### Method 1: `.env` File (Permanent)

1. Edit `.env`:
   ```env
   PERCEPTION_MODE=rule_based
   ```

2. **Restart backend** (required)

3. Frontend will auto-detect on load

### Method 2: Frontend UI (Runtime)

1. Open `Frontend/mapbox.html`
2. Find **"Agent Perception"** dropdown
3. Select desired mode
4. Changes apply immediately (no restart)

### Method 3: API Call (Programmatic)

```bash
curl -X POST http://localhost:8000/api/config/perception-mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "rule_based"}'
```

---

## LLM Fallback Behavior

### For LLM-Driven Modes

The `llm_fallback` flag is **hardcoded to `True`** for non-rule_based modes:

- If LLM call fails → Agent uses simple movement fallback
- Fallback: Continue along current edge, select next edge at intersection
- Ensures simulation continues even during LLM errors

### For Rule-Based Mode

- **No fallback needed** - algorithm is deterministic
- No external dependencies (no API calls)
- 100% reliable movement

---

## Files Using Configuration

### Backend Files

| File | Configuration Used |
|------|-------------------|
| `Backend/Agent/map_server.py` | All `.env` values, perception mode API |
| `Backend/Agent/OSM_map_server.py` | All `.env` values |
| `Backend/Agent/model.py` | `NUM_AGENTS`, `PERCEPTION_MODE`, `llm_fallback` |
| `Backend/Agent/OSM_model.py` | `NUM_AGENTS`, `PERCEPTION_MODE`, `llm_fallback` |
| `Backend/Agent/rule_based_movement.py` | Rule-based movement logic (NEW) |

### Frontend Files

| File | Configuration Used |
|------|-------------------|
| `Frontend/mapbox.html` | Fetches config from API, perception mode dropdown |
| `Frontend/index.html` | Fetches config from API |

---

## API Endpoints

### Get Current Perception Mode
```
GET /api/config/perception-mode
```
Response:
```json
{"mode": "both"}
```

### Update Perception Mode
```
POST /api/config/perception-mode
Body: {"mode": "rule_based"}
```
Response:
```json
{"status": "updated", "mode": "rule_based"}
```

### Get Full Frontend Config
```
GET /api/config/frontend
```
Response:
```json
{
  "mapbox_token": "pk...",
  "llm_provider": "gemini",
  "llm_model": "gemini-2.5-flash-lite",
  "num_agents": 50,
  "perception_mode": "both",
  "llm_fallback": true,
  "available_providers": [...]
}
```

---

## Rule-Based Movement Algorithm

Located in: `Backend/Agent/rule_based_movement.py`

### Key Functions

1. **`select_next_edge(agent, connected_edges)`**
   - Filters out current edge (prevents U-turns)
   - Sorts by global visit count
   - Returns least-visited edge

2. **`move_along_edge(agent, distance)`**
   - Updates position along edge geometry
   - Interpolates coordinates
   - Handles edge completion

3. **`make_decision(agent)`**
   - Complete movement cycle
   - Updates edge visit counts
   - Logs to tracker

4. **`step_all_agents(model)`**
   - Execute rule-based step for all agents
   - Alternative to individual `agent.step()`

### Edge Selection Logic

```python
# Pseudocode
candidates = all_connected_edges - current_edge
if no_candidates:
    candidates = all_connected_edges  # Allow U-turn

selected = min(candidates, key=lambda e: global_visit_count[e.id])
```

---

## Performance Comparison

| Mode | LLM Calls | Latency/Step | Best For |
|------|-----------|--------------|----------|
| `rule_based` | 0 | ~5-10ms | Baseline, large-scale tests |
| `amenities` | ~50 | ~500-2000ms | Destination-focused behavior |
| `perception` | ~50 | ~500-2000ms | Visual quality behavior |
| `both` | ~50 | ~500-2000ms | Full simulation |

*Latency depends on LLM provider and model*

---

## Google Cloud Platform Authentication

The system uses **Google Cloud BigQuery** to access Overture Maps data (via `overture_to_duckdb.py`).

### 4 Authentication Methods (Priority Order)

#### Method 1: API Key ⭐ EASIEST (Recommended for Development)

**Best for:** Quick setup, testing, development

1. **Create API Key:**
   - Visit: https://console.cloud.google.com/apis/credentials
   - Click "CREATE CREDENTIALS" → "API Key"
   - Copy the generated key

2. **Enable Required APIs:**
   - Visit: https://console.cloud.google.com/apis/library/bigquery.googleapis.com
   - Click "ENABLE"

3. **Update `.env`:**
   ```env
   GOOGLE_API_KEY=AIzaSy...your-api-key-here
   GOOGLE_CLOUD_PROJECT=your-gcp-project-id
   ```

4. **Restrict API Key (Recommended):**
   - Click on the API key in Credentials page
   - Under "API restrictions", select "Restrict key"
   - Select "BigQuery API" only
   - Save changes

**Pros:**
- ✅ Fastest setup (2 minutes)
- ✅ No service account management
- ✅ Easy to rotate/regenerate
- ✅ Perfect for development

**Cons:**
- ⚠️ Less secure than service accounts
- ⚠️ Not recommended for production

#### Method 2: Service Account Key (Recommended for Production)

**Best for:** Production deployments, team environments

1. **Create Service Account:**
   - Visit: https://console.cloud.google.com/iam-admin/serviceaccounts
   - Click "CREATE SERVICE ACCOUNT"
   - Grant roles: `BigQuery User`, `Storage Object Viewer`
   - Click "DONE"

2. **Create & Download Key:**
   - Click on the service account email
   - Go to "KEYS" tab
   - Click "ADD KEY" → "Create new key"
   - Select JSON format → Download

3. **Update `.env`:**
   ```env
   GOOGLE_APPLICATION_CREDENTIALS=C:/path/to/your/service-account-key.json
   GOOGLE_CLOUD_PROJECT=your-gcp-project-id
   ```

**Pros:**
- ✅ Most secure option
- ✅ Fine-grained IAM control
- ✅ Audit logging support
- ✅ Best for production

**Cons:**
- ⚠️ More setup steps
- ⚠️ Key file management required

#### Method 3: Application Default Credentials (Best for Local Dev)

**Best for:** Local development on your workstation

1. **Install Google Cloud SDK:**
   - Download: https://cloud.google.com/sdk/docs/install

2. **Authenticate Once:**
   ```bash
   gcloud auth application-default login
   ```

3. **Enable BigQuery API:**
   ```bash
   gcloud services enable bigquery.googleapis.com
   ```

4. **No `.env` changes needed** - credentials stored automatically in:
   - Windows: `%APPDATA%\gcloud\application_default_credentials.json`
   - Linux/Mac: `~/.config/gcloud/application_default_credentials.json`

**Pros:**
- ✅ No credentials in `.env` file
- ✅ Secure for local development
- ✅ Reuses your personal GCP login

**Cons:**
- ⚠️ Requires gcloud CLI installation
- ⚠️ Tied to your user account
- ⚠️ Not suitable for automated deployments

#### Method 4: GCP Project Only (Public Datasets)

**Best for:** Accessing public datasets like OpenStreetMap

For public datasets, you only need a project ID:

```env
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
```

⚠️ **Note:** You still need a GCP project with billing enabled, even for free tier.

### Verification

Test BigQuery connection:

```bash
cd Backend/Environment
python overture_to_duckdb.py
```

Expected output:
```
Setting up BigQuery client...
✓ Authenticated with API Key
  Using project: abm-view
✓ BigQuery API access confirmed
```

### Troubleshooting GCP

| Error | Solution |
|-------|----------|
| "No authentication method found" | Set `GOOGLE_API_KEY` in `.env` |
| "Could not automatically determine credentials" | Run `gcloud auth application-default login` |
| "Project not specified" | Set `GOOGLE_CLOUD_PROJECT` in `.env` |
| "BigQuery API not enabled" | Run `gcloud services enable bigquery.googleapis.com` |
| "Permission denied" | Ensure API key has BigQuery API enabled |
| "API key not valid" | Check key in Credentials page, regenerate if needed |

### Cost Information

**BigQuery Pricing (2026):**
- **First 1TB/month:** FREE
- **After 1TB:** $5/TB scanned
- **Storage:** $0.02/GB/month

**Overture Maps Data:**
- **Data Cost:** FREE (public dataset)
- **Query Cost:** Standard BigQuery rates apply
- **Typical Barcelona extraction:** ~$0.50-$2.00 (within free tier)

### Quick Comparison

| Method | Setup Time | Security | Best For |
|--------|------------|----------|----------|
| **API Key** | 2 min | Medium | Development ⭐ |
| **Service Account** | 10 min | High | Production |
| **Application Default** | 5 min | High | Local dev |
| **Project ID only** | 1 min | Low | Public data |

---

## Troubleshooting

### Backend not using new perception mode

1. Ensure backend was **restarted** after changing `.env`
2. Check backend console for:
   ```
   [INFO] Perception mode from environment: rule_based
   ```

### Frontend dropdown not working

1. Ensure backend is running
2. Check browser console for errors
3. Verify `/api/config/perception-mode` endpoint responds

### Rule-based mode not deterministic

1. Check that `NUM_AGENTS` is consistent
2. Set `SPAWN_SEED` for reproducible initial positions:
   ```env
   SPAWN_SEED=42
   ```

### LLM errors in LLM-driven modes

- Check `llm_fallback` is enabled (hardcoded to `True`)
- Agents will use simple movement on LLM failure
- Check LLM provider logs for root cause

---

## Best Practices

1. **Use `rule_based` for:**
   - Baseline comparisons
   - Performance testing
   - Large agent populations (100+)
   - Reproducible experiments

2. **Use LLM-driven modes for:**
   - Behavior richness
   - Cognitive modeling
   - Amenity preference studies
   - Urban perception research

3. **Always document:**
   - Perception mode used
   - LLM provider and model
   - Number of agents
   - Spawn seed (if set)

4. **Restart backend after:**
   - Changing `.env` values
   - Modifying perception mode in `.env`
   - Updating LLM configuration

---

## Migration from Previous Versions

If upgrading from a version without rule-based mode:

1. **Add to `.env`:**
   ```env
   PERCEPTION_MODE=both  # Default to existing behavior
   ```

2. **Update imports** (if using custom agents):
   ```python
   from rule_based_movement import make_decision as rule_based_move
   ```

3. **Frontend:** Refresh browser to get new dropdown option

---

## Additional Resources

- `Backend/Agent/rule_based_movement.py` - Rule-based movement implementation
- `Backend/LLM/llm_config.py` - LLM configuration
- `README.md` - General setup guide
- `SYSTEM_DOCUMENTATION.md` - Full system architecture
