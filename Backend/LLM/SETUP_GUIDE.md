# Quick Setup Guide for LLM Integration

## Prerequisites

1. **Ollama** - Local LLM runtime
2. **Llama 3.1** - Language model

## Installation Steps

### Step 1: Install Ollama

**Windows:**
```powershell
# Download from https://ollama.ai/download
# Or use winget:
winget install Ollama.Ollama
```

**Mac:**
```bash
brew install ollama
```

**Linux:**
```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

### Step 2: Start Ollama Service

```bash
ollama serve
```

Leave this running in a terminal.

### Step 3: Pull Llama 3.1

In another terminal:
```bash
ollama pull llama3.1
```

This will download the model (~4GB for 8B version).

### Step 4: Verify Installation

```bash
ollama list
```

You should see `llama3.1` in the list.

### Step 5: Test Ollama

```bash
ollama run llama3.1 "Hello, how are you?"
```

### Step 6: Start the Backend

```bash
cd Backend\Agent
python map_server.py
```

You should see:
```
[LLM] Available models: ['llama3.1:latest']
[LLM] Service initialized successfully
```

## Troubleshooting

### "Ollama not available"

**Check if Ollama is running:**
```bash
# Windows
tasklist | findstr ollama

# Mac/Linux
ps aux | grep ollama
```

**Start Ollama:**
```bash
ollama serve
```

### "Model not found"

Pull the model:
```bash
ollama pull llama3.1
```

### Slow Performance

**Option 1: Use smaller model**
```bash
ollama pull llama3.1:8b
```

Edit `llm_service.py`:
```python
model: str = "llama3.1:8b"
```

**Option 2: Use quantized version**
```bash
ollama pull llama3.1:7b-q4
```

### Port Conflicts

If Ollama is on a different port, edit `llm_service.py`:
```python
ollama_url: str = "http://localhost:YOUR_PORT"
```

## Testing

### Test the API

```bash
# Get LLM summary for agent 0
curl http://127.0.0.1:8000/api/agent/0/summary
```

**Expected Response:**
```json
{
  "agent_id": 0,
  "summary": "I'm Agent 0, walking through Barcelona's Eixample district...",
  "location": {"lon": 2.18024, "lat": 41.39648},
  "amenity_count": 20
}
```

### Test in Browser

1. Open Frontend/index.html
2. Click on any agent
3. Wait 1-2 seconds
4. See natural language summary appear

## Performance Expectations

| Model | RAM | Speed | Quality |
|-------|-----|-------|---------|
| llama3.1:8b | 8GB | ~1s | Good |
| llama3.1:7b | 4GB | ~2s | Decent |
| llama3.1:70b | 40GB | ~5s | Excellent |

## Configuration Options

Edit `Backend/LLM/llm_service.py`:

```python
# Model selection
model: str = "llama3.1"  # or "llama3.1:8b", "llama3.1:70b"

# Temperature (creativity)
"temperature": 0.7,  # 0.0-1.0, higher = more creative

# Max tokens (length)
"max_tokens": 150,  # Increase for longer summaries

# Timeout
timeout=10  # Seconds to wait for response
```

## Fallback Mode

If Ollama is not available, the system automatically uses template-based summaries:

```
"Agent 0: I can see Joys cafe (cafe), Domino's (fast_food), and a drinking_water nearby."
```

No LLM required, instant responses.

## Production Deployment

For production, consider:

1. **Use a dedicated LLM server**
2. **Cache summaries** for frequently visited locations
3. **Rate limit** LLM calls
4. **Use async processing** with a queue
5. **Monitor token usage** and costs

## Next Steps

- [x] Install Ollama
- [x] Pull Llama 3.1
- [x] Test backend API
- [x] View summaries in frontend
- [ ] Customize prompts in `llm_service.py`
- [ ] Adjust temperature/tokens for your needs
- [ ] Consider caching strategy for production
