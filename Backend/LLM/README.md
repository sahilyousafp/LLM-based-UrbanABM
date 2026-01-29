# LLM Backend for Agent Perspective

This module provides LLM-powered natural language summaries of agent perspectives using Ollama and Llama 3.1.

## Requirements

1. **Ollama** must be installed and running
2. **Llama 3.1** model must be available

## Setup

### 1. Install Ollama

Download from: https://ollama.ai/

### 2. Pull Llama 3.1

```bash
ollama pull llama3.1
```

### 3. Verify Ollama is running

```bash
ollama list
```

Should show llama3.1 in the list.

### 4. Install Python dependencies

```bash
pip install requests
```

## Usage

The LLM service is automatically integrated into the main backend API.

### Endpoint

```
GET /api/agent/{agent_id}/summary
```

Returns a natural language summary of what the agent sees.

**Example Response:**
```json
{
  "agent_id": 302,
  "summary": "I'm Agent 302, walking through a vibrant part of Barcelona. Right now I can see Joys cafe just 25 meters away, perfect for a quick coffee break. There's also a Domino's nearby if I'm feeling hungry, and I notice a few vending machines scattered around - this seems like a well-serviced neighborhood with plenty of amenities within easy reach."
}
```

## How It Works

1. Frontend requests agent summary every 3 seconds
2. Backend fetches agent's nearby amenities
3. LLM service formats the data into a prompt
4. Ollama/Llama 3.1 generates natural language summary
5. Summary is returned to frontend and displayed

## Fallback Mode

If Ollama is not available, the service falls back to template-based summaries:

```
"Agent 302: I can see Joys cafe (cafe), Domino's (fast_food), and a drinking_water nearby."
```

## Configuration

Edit `llm_service.py` to customize:

- **Ollama URL**: Default is `http://localhost:11434`
- **Model**: Default is `llama3.1` (can use `llama3.1:8b`, `llama3.1:70b`, etc.)
- **Temperature**: Default is 0.7 (0.0 = deterministic, 1.0 = creative)
- **Max Tokens**: Default is 150

## Troubleshooting

### "Ollama not available"

Make sure Ollama is running:
```bash
ollama serve
```

### "Model not found"

Pull the model:
```bash
ollama pull llama3.1
```

### Slow responses

- Use a smaller model: `llama3.1:8b`
- Reduce max_tokens in `llm_service.py`
- Check your GPU/CPU usage

## Performance

- **Cold start**: ~2-5 seconds (first request)
- **Warm**: ~0.5-2 seconds (subsequent requests)
- **Fallback**: <0.1 seconds (no LLM)

## Examples

### With LLM (Natural):
> "I'm walking through a lively neighborhood in Barcelona's Eixample district. I can see Joys cafe nearby where I might grab a coffee, and there's a Domino's just around the corner. The area feels well-serviced with several vending machines and a pharmacy within view."

### Without LLM (Template):
> "Agent 302: I can see Joys cafe (cafe), Domino's (fast_food), and a drinking_water nearby."
