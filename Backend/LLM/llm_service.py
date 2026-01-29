"""
LLM Service for Agent Perspective Summarization
Uses Ollama with local Llama 3.1 to generate natural language summaries
"""
import requests
import json
from typing import List, Dict

class LLMService:
    """Service to interact with Ollama for generating agent perspective summaries"""
    
    def __init__(self, ollama_url: str = "http://localhost:11434", model: str = "llama3.1"):
        self.ollama_url = ollama_url
        self.model = model
        self.api_endpoint = f"{ollama_url}/api/generate"
    
    def check_availability(self) -> bool:
        """Check if Ollama is running and model is available"""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=2)
            if response.status_code == 200:
                models = response.json().get("models", [])
                available_models = [m.get("name", "") for m in models]
                print(f"[LLM] Available models: {available_models}")
                
                # Check if our model is available (with or without tag)
                model_available = any(self.model in m for m in available_models)
                if not model_available:
                    print(f"[LLM] WARNING: {self.model} not found in available models")
                    print(f"[LLM] You may need to run: ollama pull {self.model}")
                return model_available
            return False
        except Exception as e:
            print(f"[LLM] Ollama not available: {e}")
            return False
    
    def summarize_agent_perspective(self, agent_data: Dict) -> str:
        """
        Generate a natural language summary of what the agent sees
        
        Args:
            agent_data: Dictionary containing agent info and nearby_amenities list
            
        Returns:
            Natural language summary string
        """
        # Extract agent information
        agent_id = agent_data.get("id", "Unknown")
        agent_type = agent_data.get("type", "Agent")
        location = agent_data.get("location", {})
        nearby = agent_data.get("nearby_amenities", [])
        
        # Build context for LLM
        if not nearby:
            return f"I'm agent {agent_id}, walking through the city. Currently, I don't see any notable places nearby."
        
        # Create a structured description of nearby amenities
        amenities_desc = []
        for item in nearby[:10]:  # Limit to top 10 for prompt
            name = item.get("name", "an unnamed place")
            amenity_type = item.get("type", "location")
            distance = item.get("dist", 0)
            
            if name == "nan" or name == "Unnamed":
                amenities_desc.append(f"{amenity_type} ({distance:.0f}m)")
            else:
                amenities_desc.append(f"{name} - {amenity_type} ({distance:.0f}m)")
        
        amenities_text = ", ".join(amenities_desc)
        
        # Create prompt for LLM
        prompt = f"""You are Agent {agent_id}, a pedestrian in Barcelona's Eixample district at coordinates (lon: {location.get('lon', 0):.5f}, lat: {location.get('lat', 0):.5f}).

Nearby places: {amenities_text}

Write 3-4 sentences about what you see and your surroundings. Use **bold** (markdown) for all place names and amenity types. Be descriptive but direct. Focus on the closest or most interesting places."""

        try:
            # Call Ollama API
            response = requests.post(
                self.api_endpoint,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.7,
                        "top_p": 0.9,
                        "max_tokens": 150  # 3-4 sentences
                    }
                },
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                summary = result.get("response", "").strip()
                return summary if summary else "I'm exploring the neighborhood..."
            else:
                print(f"[LLM] API Error: {response.status_code}")
                return self._fallback_summary(agent_id, nearby)
                
        except requests.exceptions.Timeout:
            print(f"[LLM] Timeout - using fallback")
            return self._fallback_summary(agent_id, nearby)
        except Exception as e:
            print(f"[LLM] Error: {e}")
            return self._fallback_summary(agent_id, nearby)
    
    def _fallback_summary(self, agent_id: int, nearby: List[Dict]) -> str:
        """Fallback summary when LLM is not available"""
        if not nearby:
            return f"Agent {agent_id}: Walking through the city, no notable places nearby."
        
        # Simple template-based summary with bold formatting
        top_places = nearby[:3]
        place_parts = []
        
        for place in top_places:
            name = place.get("name", "")
            ptype = place.get("type", "location")
            dist = place.get("dist", 0)
            
            if name and name != "nan" and name != "Unnamed":
                place_parts.append(f"**{name}** ({ptype}, {dist:.0f}m)")
            else:
                place_parts.append(f"**{ptype}** ({dist:.0f}m)")
        
        if len(place_parts) == 1:
            return f"I'm near {place_parts[0]}."
        elif len(place_parts) == 2:
            return f"I can see {place_parts[0]} and {place_parts[1]}."
        else:
            return f"I'm near {place_parts[0]}, {place_parts[1]}, and {place_parts[2]}."

# Global instance
llm_service = None

def get_llm_service() -> LLMService:
    """Get or create the LLM service singleton"""
    global llm_service
    if llm_service is None:
        llm_service = LLMService()
        if llm_service.check_availability():
            print("[LLM] Service initialized successfully")
        else:
            print("[LLM] Service running in fallback mode (Ollama not available)")
    return llm_service
