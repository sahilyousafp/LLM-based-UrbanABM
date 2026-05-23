# StreetPLM Output Variables & Metrics Reference

This document describes each variable in the StreetPLM perceptual urban-quality
analysis output, what it measures, how it maps to individual comfort research,
and the authoritative sources behind each metric.

The system analyses each street-view image **as a whole** and returns descriptive
text focused on **individual comfort criteria** — things that cannot be derived from
GIS, cadastral, or OpenStreetMap data and can only be assessed from street-level imagery.

---

## Design Rationale

Traditional data sources can already provide:
- **Building typology / height / use** → Cadastre, OSM, Overture Maps
- **Street geometry / width** → OSM, Overture road network
- **Land use / zoning** → Municipal GIS layers
- **Amenity locations** → Overture Places, Google Places API

What they **cannot** provide is how a street **feels** to walk through. This is
what StreetPLM captures — the subjective, perceptual, experiential layer of
individual comfort: cleanliness, visibility, safety, lighting, greenery, and more.

---

## Output Schema

The JSON output has two top-level sections:
- `metadata` — geocoordinates, heading, street name, model info, latency
- `scene_analysis` — 16 comfort-focused perceptual fields described below

---

## Perceptual Comfort Fields (16 total)

### `scene_context` *(nested object)*
**Type:** JSON object with 4 quadrant keys: `top_left`, `top_right`, `bottom_left`, `bottom_right`
**Description:** The image is divided into 4 quadrants and each is described in one sentence,
capturing what is present and how it contributes to comfort or discomfort.
- `top_left` — upper-left portion of the scene (sky, building tops, canopy)
- `top_right` — upper-right portion of the scene
- `bottom_left` — ground-level left side (pavement, obstacles, activity)
- `bottom_right` — ground-level right side
**Purpose:** Provides spatially-grounded scene context for interpreting all other fields.
**Source framework:**
- *Lynch, K. (1960) The Image of the City* — legibility and imageability.
- *Gehl, J. (2010) Cities for People* — human-scale scene reading.

---

### `perceived_safety`
**Description:** How safe does the street feel to walk through?
**What to capture:** Sightlines, "eyes on the street" (active windows, ground-floor shops),
hiding spots, signs of vandalism or neglect, broken windows, and whether the space
feels supervised or isolated.
**Source framework:**
- *Jacobs, J. (1961) The Death and Life of Great American Cities* — "eyes on the street".
- *Wilson & Kelling (1982) "Broken Windows"* — visible disorder as a safety signal.
- *Newman, O. (1972) Defensible Space* — natural surveillance and territorial markers.

---

### `visibility`
**Description:** How far and clearly can a pedestrian see along this street?
**What to capture:** Sightline depth, visual obstructions (parked vehicles, scaffolding,
bends, vegetation blocking views), daytime clarity, and whether the environment feels
legible and easy to navigate.
**Why separate from lighting:** Visibility covers daytime sightline quality and spatial
legibility; lighting_quality covers artificial light and nighttime comfort.
**Source framework:**
- *Ewing & Clemente (2013) Measuring Urban Design* — imageability and sightlines.
- *CPTED guidelines* — visibility as a primary crime-deterrence factor.

---

### `lighting_quality`
**Description:** How well-lit is the street for pedestrian comfort?
**What to capture:** Streetlight presence and spacing, shopfront illumination, shadow
pools, dark corners or underpasses, and overall light quality both day and night.
**Source framework:**
- *Painter, K. (1996) "The influence of street lighting on crime and fear of crime"*.
- *CPTED guidelines* — Crime Prevention Through Environmental Design lighting standards.
- *Ewing & Clemente (2013)* — sensory richness and lighting atmosphere.

---

### `cleanliness`
**Description:** How visually clean and maintained does the street appear?
**What to capture:** Litter, graffiti/tags, stained surfaces, overflowing bins,
construction debris, weeds in cracks. Conversely — fresh paint, clean shopfronts,
swept sidewalks, manicured planters.
**Source framework:**
- *Wilson & Kelling (1982) "Broken Windows"* — physical disorder perception.
- *Ewing & Clemente (2013)* — upkeep as a walkability quality.
- *Sampson, R.J. & Raudenbush, S.W. (1999)* — systematic social observation of disorder.

---

### `greenery`
**Description:** How does vegetation contribute to pedestrian comfort and wellbeing?
**What to capture:** Tree canopy maturity and coverage, planter boxes, ground cover,
climbing plants, absence of green, whether trees actually shade the sidewalk, and
the sensory relief of vegetation vs a stark mineral environment.
**Source framework:**
- *Ewing & Handy (2009)* — tree canopy as the single strongest positive walkability
  predictor in perception studies.
- *Kaplan, R. & Kaplan, S. (1989)* — restorative environments and biophilia.
- *Barcelona Green Infrastructure Plan (2017)* — target canopy cover per street type.

---

### `thermal_comfort`
**Description:** How thermally comfortable is the street to walk?
**What to capture:** Tree shade coverage, awning presence, sun exposure, building
shadow patterns, wind indicators (flags, awning movement), covered areas.
**Source framework:**
- *Gehl, J. (2010)* — microclimate as the primary determinant of outdoor comfort.
- *Nikolopoulou, M. & Lykoudis, S. (2006)* — thermal comfort in outdoor urban spaces.
- *Oke, T.R. (1987) Boundary Layer Climates* — urban canyon effects on temperature.

---

### `walkability`
**Description:** How easy and safe is it to walk this stretch?
**What to capture:** Pavement condition, surface evenness, obstacles (parked vehicles,
poles, bins, construction), curb cuts, sidewalk width relative to use, tripping hazards.
**Source framework:**
- *Speck, J. (2012) Walkable City* — the "comfortable walk" and sidewalk quality.
- *Ewing & Handy (2009) "Measuring the Unmeasurable"* — walkability perceptual factors.
- *Barcelona Accessibility Plan* — universal design surface standards.

---

### `noise_comfort`
**Description:** What visual cues suggest the acoustic comfort level of this street?
**What to capture:** Traffic volume (visible vehicles), construction activity,
outdoor dining, street width (narrow = more sound reflection), acoustic barriers,
trees as sound dampeners, motorcycles/bikes.
**Source framework:**
- *Kang, J. (2006) Urban Sound Environment* — visual-acoustic correlations.
- *EU Environmental Noise Directive* — noise mapping methodologies.
- *Gehl, J. (2010)* — distances for conversation as a noise proxy.

---

### `crowding`
**Description:** How crowded or empty does the street appear?
**What to capture:** Pedestrian density, available walking space, bottlenecks,
queues, whether the street feels comfortably populated, uncomfortably dense, or deserted.
**Source framework:**
- *Gehl & Svarre (2013) How to Study Public Life* — pedestrian counting and flow.
- *Whyte, W.H. (1980) The Social Life of Small Urban Spaces* — life between buildings.
- *Fruin, J.J. (1971) Pedestrian Planning and Design* — Level of Service.

---

### `privacy`
**Description:** How exposed or private does a pedestrian feel here?
**What to capture:** Overlooking windows and balconies, CCTV cameras, sight angles,
open vs narrow corridor feel, whether one feels watched or anonymous.
**Source framework:**
- *Alexander, C. et al. (1977) A Pattern Language* — Degrees of Publicness.
- *Altman, I. (1975) The Environment and Social Behavior* — privacy regulation theory.

---

### `social_potential`
**Description:** Does the street encourage stopping, lingering, and social interaction?
**What to capture:** Benches, café terraces, ledges to sit on, steps, small plazas,
gathering spots. Conversely — no seating, hostile architecture, anti-loitering design.
**Source framework:**
- *Gehl, J. (2010)* — optional and social activities as indicators of space quality.
- *Whyte, W.H. (1980)* — sittable space and "triangulation".
- *Project for Public Spaces (PPS)* — "Power of 10" placemaking framework.

---

### `visual_interest`
**Description:** How visually stimulating and varied is the streetscape?
**What to capture:** Facade diversity, colour palette, window displays, street art,
architectural details at eye level vs monotonous blank walls, repetitive surfaces.
**Source framework:**
- *Ewing & Handy (2009)* — complexity as a perceptual dimension of walkability.
- *Lynch, K. (1960)* — imageability — the quality that makes a place memorable.
- *Gehl, J. (2010)* — 5 km/h architecture: detail at eye level.

---

### `enclosure_exposure`
**Description:** Does the street feel spatially contained and intimate, or wide-open and exposed?
**What to capture:** Building height to street width ratio (felt, not measured),
sky visibility, canopy ceiling effect, spatial proportion, whether one feels protected
or windswept and exposed.
**Source framework:**
- *Ewing & Handy (2009)* — enclosure as a key perceptual quality.
- *Jacobs, A.B. (1993) Great Streets* — spatial proportions that create comfort.
- *Alexander, C. et al. (1977) A Pattern Language* — Pattern #106 Positive Outdoor Space.

---

### `accessibility`
**Description:** How accessible is this street for users with mobility or visual impairments?
**What to capture:** Ramps, curb cuts, tactile paving, bollard spacing, step-free paths,
wheelchair passability, uneven surfaces, temporary barriers, construction blocking paths.
**Source framework:**
- *Barcelona Accessibility Plan* — universal design guidelines.
- *ADA Accessibility Guidelines (ADAAG)* — sidewalk and ramp standards.
- *Inclusive Design standards (BS 8300)* — detailed pedestrian access requirements.

---

### `street_activity`
**Description:** What activities are happening on the street?
**What to capture:** Distinguish *necessary* (commuting, carrying groceries), *optional*
(sitting, window shopping, reading), and *social* (talking, eating at terraces, playing)
activities. Note their positions and effect on atmosphere.
**Source framework:**
- *Gehl, J. (2010)* — the three types of outdoor activity (necessary, optional, social).
- *Mehta, V. (2008) "Walkable Streets"* — activity diversity as a walkability indicator.
- *Whyte, W.H. (1980)* — what draws people to stay and interact.

---

## Reliability & Limitations

| Factor | Impact on PLM Accuracy |
|--------|----------------------|
| Image quality | 640×640 px Street View provides adequate detail for macro features |
| Lighting / time of day | Shadows and night images affect visibility and lighting assessments |
| Temporal snapshot | Crowding, activity, and noise represent ONE moment |
| Occlusion | Parked vehicles, scaffolding can mask ground-level features |
| Model capacity | PLM-1B excels at scene-level perception; subtle cues (tactile paving, CCTV) may be missed |
| Subjectivity | Perceptual qualities are inherently subjective — cross-validate with surveys |

**Recommended validation approach:**
1. Cross-reference `perceived_safety` with Barcelona crime incident data (Mossos d'Esquadra).
2. Validate `crowding` against pedestrian counter data where available.
3. Validate `noise_comfort` against Barcelona noise maps (Mapa de Soroll).
4. Benchmark `thermal_comfort` against Urban Heat Island studies (Barcelona Ecology Agency).
5. Validate `greenery` with NDVI satellite data and i-Tree canopy assessments.
6. Conduct stated-preference surveys matching each comfort field for ground-truth.
