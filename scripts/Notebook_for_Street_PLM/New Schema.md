Markdown

\# Task: Restructure StreetPLM Pydantic Schema and Prompt to Egocentric Corridor Layout



We are updating our vision-based urban scene understanding pipeline (`street\_plm\_job.py`) from a scattered array-based architecture to an egocentric, sequential lane-by-lane layout structure. This makes it far more intuitive for downstream LLM pathfinding agents to navigate. 



You must rewrite the Pydantic classes and the structural prompt tracking constraints in our script to implement this new layout, ensuring that \*\*no data variables or enum classifications from our original pipeline are dropped.\*\*



\---



\### 1. Updated Pydantic Schema Definition



Replace the old multi-array `StreetSceneAnalysis` and sub-models with this unified layout structure:



```python

from typing import List, Literal, Optional

from pydantic import BaseModel, Field



class BuiltEnvironmentContext(BaseModel):

&#x20;   enclosure: Literal\["open", "semi", "enclosed", "unknown"] = "unknown"

&#x20;   architectural\_style: Literal\["neo\_gothic", "modernist", "contemporary", "neoclassical", "vernacular", "eclectic", "art\_deco", "other", "unknown"] = "unknown"

&#x20;   building\_condition: Literal\["excellent", "good", "fair", "poor", "under\_construction", "unknown"] = "unknown"

&#x20;   storefront\_type: Literal\["retail", "restaurant", "cafe", "office", "residential", "hotel", "vacant", "cultural", "industrial", "other", "unknown"] = "unknown"

&#x20;   architectural\_details: Optional\[str] = None



class LaneCorridorState(BaseModel):

&#x20;   # Core Spatial Layout

&#x20;   type: Literal\["sidewalk", "main\_roadway", "shared\_bus\_lane", "bicycle\_lane", "median", "unknown"] = "unknown"

&#x20;   width: Literal\["narrow", "moderate", "wide", "unknown"] = "unknown"

&#x20;   passability: Literal\["clear", "caution", "obstructed", "blocked"] = "clear"

&#x20;   

&#x20;   # Environment Analytics (Preserved from the old scattered fields)

&#x20;   lighting\_condition: Literal\["dark", "dim", "adequate", "bright", "unknown"] = "adequate"

&#x20;   crowd\_density: Literal\["empty", "sparse", "moderate", "dense", "unknown"] = "empty"

&#x20;   greenery\_coverage: Literal\["none", "sparse", "moderate", "dense", "unknown"] = "none"

&#x20;   

&#x20;   # Material features and lists

&#x20;   assets: List\[str] = Field(

&#x20;       default\_factory=list, 

&#x20;       description="Fixed physical infrastructure elements (e.g., street\_lamp, planters, bicycle\_rack, trash\_bins)."

&#x20;   )

&#x20;   hazards: List\[str] = Field(

&#x20;       default\_factory=list, 

&#x20;       description="Dynamic obstructions or routing conflicts (e.g., stopped\_delivery\_van, pedestrians, cyclists)."

&#x20;   )

&#x20;   

&#x20;   # Built Environment Context (Populated natively if zone behaves as a sidewalk or pedestrian area)

&#x20;   built\_context: Optional\[BuiltEnvironmentContext] = None



class TrafficControlSign(BaseModel):

&#x20;   text: str = Field(description="The exact text or alphanumeric code transcribed from the sign or board.")

&#x20;   type: Literal\["sign", "signage", "board", "banner", "label", "graffiti", "information"]

&#x20;   applies\_to: str = Field(description="Vehicular class, target destination, or lane routing constraint implied.")



class RestructuredStreetSceneAnalysis(BaseModel):

&#x20;   scene: str = Field(description="A concise one-sentence summary of the overall street scene context.")

&#x20;   egocentric\_corridor: dict\[Literal\["far\_left", "left", "center", "right", "far\_right"], LaneCorridorState] = Field(

&#x20;       description="A strict left-to-right horizontal decomposition of the physical streetscape topology."

&#x20;   )

&#x20;   visible\_traffic\_controls: List\[TrafficControlSign] = Field(default\_factory=list)

2\. Updated Prompt Variable String

Update the prompt construction string blocks within the file to instruct the vision-language model on how to map into this nested schema format:



Python

FULL\_PROMPT = (

&#x20;   "You are an expert urban analyst and automated vehicle routing mapper. Study the street-level "

&#x20;   "image and output ONLY a single JSON object matching the required topological egocentric corridor format. "

&#x20;   "Deconstruct the scene horizontally across 5 continuous spatial zones: \[far\_left, left, center, right, far\_right].\\n\\n"

&#x20;   "Output ONLY a single valid JSON object structured exactly like this:\\n"

&#x20;   "{\\n"

&#x20;   '  "scene": "<one sentence overall description of the street landscape>",\\n'

&#x20;   '  "egocentric\_corridor": {\\n'

&#x20;   '    "far\_left": {\\n'

&#x20;   '      "type": "sidewalk|main\_roadway|shared\_bus\_lane|bicycle\_lane|median|unknown",\\n'

&#x20;   '      "width": "narrow|moderate|wide|unknown",\\n'

&#x20;   '      "passability": "clear|caution|obstructed|blocked",\\n'

&#x20;   '      "lighting\_condition": "dark|dim|adequate|bright",\\n'

&#x20;   '      "crowd\_density": "empty|sparse|moderate|dense",\\n'

&#x20;   '      "greenery\_coverage": "none|sparse|moderate|dense",\\n'

&#x20;   '      "assets": \["element\_1", "element\_2"],\\n'

&#x20;   '      "hazards": \[],\\n'

&#x20;   '      "built\_context": {\\n'

&#x20;   '        "enclosure": "open|semi|enclosed",\\n'

&#x20;   '        "architectural\_style": "modernist|contemporary|neoclassical|art\_deco|other",\\n'

&#x20;   '        "building\_condition": "excellent|good|fair|poor",\\n'

&#x20;   '        "storefront\_type": "retail|restaurant|cafe|residential|office"\\n'

&#x20;   '      }\\n'

&#x20;   '    },\\n'

&#x20;   '    "left": { ... },\\n'

&#x20;   '    "center": { ... },\\n'

&#x20;   '    "right": { ... },\\n'

&#x20;   '    "far\_right": { ... }\\n'

&#x20;   '  },\\n'

&#x20;   '  "visible\_traffic\_controls": \[\\n'

&#x20;   '    {"text": "...", "type": "sign|board|banner|graffiti", "applies\_to": "..."}\\n'

&#x20;   '  ]\\n'

&#x20;   "}\\n\\n"

&#x20;   "CRITICAL STRUCTURAL INSTRUCTIONS:\\n"

&#x20;   "1. Do not break tracking elements out into root-level array lists. Categorical attributes (lighting, crowdedness, greenery) must be populated explicitly inside their respective spatial zone object.\\n"

&#x20;   "2. If a specific zone contains no physical assets or dynamic hazards, return them as empty arrays \[].\\n"

&#x20;   "3. If a zone is a roadway and contains no storefronts or building facades, omit the 'built\_context' dictionary or pass it as null.\\n"

&#x20;   "4. Return strictly valid raw JSON format. No explanations, no markdown wrapper backticks."

)

3\. Execution Requirements for the Rewrite

Verify Complete Replacement: Locate where StreetSceneAnalysis was originally called or parsed in street\_plm\_job.py and cleanly point the pipeline generation parser to use the new RestructuredStreetSceneAnalysis class.



Ensure Parameter Safety: Retain all environment variables, pipeline loop logging blocks, BigQuery extraction layers, and output path mappings.



Keep the Code Operational: Do not omit dependencies, processing exceptions, or local file saving logics. Ensure the code remains syntax-valid and ready for execution on a remote multi-GPU runner.

