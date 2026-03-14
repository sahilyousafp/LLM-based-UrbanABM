# StreetPLM Output Variables & Metrics Reference

This document describes each variable in the StreetPLM full-image scene analysis
output, what it measures, how it maps to urban walkability research, and the
authoritative sources behind each metric.

The system analyses each street-view image **as a whole** (no quadrant division)
and returns purely descriptive text — no numeric ratings or scores.

---

## Scene Analysis Fields

All fields are **descriptive strings** that include spatial location references
(left, right, center, foreground, background) to ground each observation in the image.

### `scene_overview`
**Description:** A 1-2 sentence overview of the entire street scene.
**What to capture:** The dominant impression — street type, scale, mood, key features.
**Source framework:**
- *Lynch, K. (1960) The Image of the City* — legibility and imageability of streetscapes.
- *Gehl, J. (2010) Cities for People* — human-scale first impressions.
**PLM guidance:** Describe as if writing a caption for a photography exhibition.

### `buildings`
**Description:** Building forms visible in the image, with their positions and typologies.
**What to capture:** Height, typology (residential, mixed-use, commercial, institutional),
architectural style, notable façade features.
**Source framework:**
- *COAC (Col·legi d'Arquitectes de Catalunya)* — Barcelona architectural heritage catalogues.
- *Gehl, J. (2010)* — ground-floor use typology and its effect on street life.
- *Barcelona Urban Ecology Agency* — Eixample block classification.
**PLM guidance:** Describe each visible building façade with its position (left/right/background).

### `materials`
**Description:** Primary construction materials visible in the scene.
**What to capture:** Façade materials, surface textures, material diversity.
**Source framework:**
- *Ewing & Handy (2009) "Measuring the Unmeasurable"* — material richness in streetscape perception.
- *Lynch, K. (1960)* — material texture as a legibility cue.
**PLM guidance:** Note material variety and where each material appears.

### `building_condition`
**Description:** Visual maintenance state of building surfaces.
**What to capture:** Signs of upkeep, deterioration, recent restoration, graffiti.
**Source framework:**
- *Wilson & Kelling (1982) "Broken Windows"* — physical disorder perception.
- *Ewing & Clemente (2013) "Measuring Urban Design"* — upkeep as a walkability factor.
**PLM guidance:** Describe condition differences across different parts of the image.

### `street_furniture`
**Description:** Urban furniture items visible, with their positions.
**What to capture:** Benches, streetlights, bollards, litter bins, bus stops, bike racks,
planters, kiosks, drinking fountains.
**Source framework:**
- *Gehl, J. (2010)* — necessary vs optional street furniture and its role in lingering.
- *Project for Public Spaces (PPS)* — placemaking comfort indicators.
- *Barcelona Superblock guidelines* — furniture inventory standards.
**PLM guidance:** List what you see and WHERE it sits in the image.

### `vegetation`
**Description:** Green elements visible in the scene with their positions.
**What to capture:** Tree species/maturity, canopy coverage, potted plants, climbing plants,
ground cover, absence of greenery.
**Source framework:**
- *Ewing & Handy (2009)* — tree canopy as the single strongest positive walkability predictor.
- *Barcelona Green Infrastructure Plan (2017)* — target canopy cover per street typology.
- *i-Tree Canopy (USDA Forest Service)* — standardised urban canopy assessment.
**PLM guidance:** Describe type, density, maturity, and position of visible greenery.

### `signage`
**Description:** Signs, shop names, traffic signs, wayfinding elements with positions.
**What to capture:** Commercial signage, traffic regulation signs, wayfinding,
cultural markers, language.
**Source framework:**
- *Lynch, K. (1960)* — signage as a legibility and orientation cue.
- *Walk Score methodology* — commercial signage density as a walkability proxy.
**PLM guidance:** List specific signs visible and where they appear.

### `ground_surfaces`
**Description:** Sidewalk, road, and other ground surfaces visible.
**What to capture:** Material, width, condition, crosswalks, curb cuts, tactile paving.
**Source framework:**
- *Speck, J. (2012) Walkable City* — sidewalk quality as foundational walkability.
- *Barcelona Accessibility Plan* — universal design ground surface standards.
**PLM guidance:** Describe left sidewalk, road, right sidewalk separately.

### `spatial_enclosure`
**Description:** Perceived spatial containment and depth of the street scene.
**What to capture:** Building height-to-street width ratio, canopy softening,
sight lines, perspective depth.
**Source framework:**
- *Ewing & Handy (2009)* — enclosure ratio (building height ÷ street width).
- *Jacobs, A.B. (1993) Great Streets* — spatial proportions and street character.
- *Alexander, C. et al. (1977) A Pattern Language* — patterns for pedestrian streets.
**PLM guidance:** Describe the feeling of containment versus openness.

### `pedestrian_activity`
**Description:** People visible in the scene and what they're doing.
**What to capture:** Number of people, activities (walking, sitting, cycling),
social interaction, age groups if visible.
**Source framework:**
- *Gehl & Svarre (2013) How to Study Public Life* — pedestrian flow estimation.
- *Space Syntax* — pedestrian movement and spatial configuration.
**PLM guidance:** Describe specific people and activities you can identify.

### `lighting_atmosphere`
**Description:** Natural lighting conditions and atmospheric quality.
**What to capture:** Time of day indicators, shadow patterns, light quality,
weather, colour temperature.
**Source framework:**
- *Ewing & Clemente (2013)* — sensory richness in streetscape perception.
- *Gehl (2010)* — microclimate and comfort conditions.
**PLM guidance:** Describe the light quality and mood it creates.

---

## Viewer Perspective Fields

These four fields provide **purely subjective, first-person narratives** of the
same scene as experienced by different walker archetypes. No ratings — only
qualitative descriptions of what each viewer type would notice and feel.

### `as_resident`
**Archetype:** Daily inhabitant who values familiar, comfortable, service-rich streets.
**Notices:** Grocery shops, pharmacies, building condition, shade, noise,
sidewalk width, sense of safety, cleanliness.
**Source framework:**
- *Mehta, V. (2008) "Walkable Streets"* — resident comfort and convenience needs.
- *Gehl (2010)* — necessary activities (daily errands) as baseline walkability.

### `as_commuter`
**Archetype:** Efficient walker who values directness, speed, and obstacle-free paths.
**Notices:** Path width, surface quality, sightlines, congestion, obstacles,
route clarity, crossing opportunities.
**Source framework:**
- *Speck (2012)* — the "useful walk" — getting somewhere efficiently.
- *Wunderlich, F.M. (2008) "Walking and Rhythmicity"* — purposive walking tempo.

### `as_tourist`
**Archetype:** Exploratory walker drawn to novelty, heritage, and visual interest.
**Notices:** Architectural details, historic features, colours, cultural signage,
photo opportunities, café terraces, unique streetscape character.
**Source framework:**
- *de Certeau, M. (1984) The Practice of Everyday Life* — the flâneur's gaze.
- *Urry, J. (2002) The Tourist Gaze* — visual consumption of place.

### `as_student`
**Archetype:** Social walker who values lively, affordable, green, study-friendly spaces.
**Notices:** Café terraces, park benches, library proximity, social energy,
affordable food options, Wi-Fi signs, shaded reading spots.
**Source framework:**
- *Gehl (2010)* — optional and social activities in public space.
- *Mehta (2008)* — social walkability dimensions.

---

## Reliability & Limitations

| Factor | Impact on PLM Accuracy |
|--------|----------------------|
| Image quality | 640×640 px Street View provides adequate detail for macro features; fine signage text may be missed |
| Lighting / time of day | Shadows and night images significantly degrade colour and condition accuracy |
| Occlusion | Parked vehicles, construction scaffolding can mask ground-floor features |
| Model capacity | PLM-1B is a 1B-parameter model — it excels at coarse scene description but may confuse similar architectural styles |
| Viewer perspectives | Generated subjectively by the model — cross-reference with actual user surveys for validation |

**Recommended validation approach:**
1. Cross-reference `buildings` with Barcelona cadastral data (Cadastre/SEC).
2. Validate `vegetation` with NDVI satellite data or i-Tree assessments.
3. Benchmark viewer perspectives against stated-preference surveys for each archetype.
4. Spot-check `signage` against Google Places API POI data.
