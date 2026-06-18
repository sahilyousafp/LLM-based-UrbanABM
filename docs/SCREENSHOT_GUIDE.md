# Screenshot Capture Guide

Screenshots needed for `README.md`. Save each to `docs/images/` with the filename below.

**Setup:** Browser at 1400x900, dark theme, backend running with 15+ agents spawned.

---

## Checklist

### 1. `hero_map_view.png`
- **Where:** Panel 5 (Multi-Agent), after spawning ~15 agents and running 20+ steps
- **Show:** Full browser window — map with agent dots visible, building footprints, walk network lines, panel sidebar visible on left
- **Tip:** Zoom to show a cluster of agents near Passeig de Gracia for visual density

### 2. `panel3_personality.png`
- **Where:** Panel 3 (Personality Editor)
- **Show:** One archetype selected (e.g., Tourist), profile form visible on the right, daily plan section expanded
- **Tip:** Fill in a representative profile so the form looks populated, not empty

### 3. `panel4_single_agent.png`
- **Where:** Panel 4 (Single Agent Lab), after placing start + target and running 10+ steps
- **Show:** Agent profile card on left, emotion mix pie chart, cognition metrics, trail visible on map
- **Tip:** Pick a tourist archetype for colorful cognition state; ensure the trail line is visible on the map

### 4. `panel5_multi_agent.png`
- **Where:** Panel 5 (Multi-Agent), with agents running
- **Show:** Spawn controls sidebar (count slider, archetype mix), agents moving on map, time-of-day banner visible
- **Tip:** Use archetype mix with all 4 types so the map shows multiple colors

### 5. `settings_llm.png`
- **Where:** Settings drawer (click gear icon in top bar), LLM tab selected
- **Show:** Provider dropdown visible, model field, the available providers list
- **Tip:** Crop to just the settings drawer (not full browser) for a cleaner look. ~600px wide.

### 6. `map_layers.png`
- **Where:** Any panel with map visible, zoom level ~15
- **Show:** Building footprints (filled), walk network lines (purple), amenity dots, Street View grid points (cyan), at least a few agent dots
- **Tip:** Enable all layers in Settings > Map. This is the "everything on" view.

### 7. `agent_perception.png` *(optional)*
- **Where:** Panel 4, expand the "What The Agent Sees" perception card
- **Show:** Street View image (if available) + VLM analysis text fields (scene overview, buildings, vegetation)
- **Tip:** Only works if Street View images have been downloaded and analyzed for the area

### 8. `overture_download.png` *(optional)*
- **Where:** Click the Overture Maps button (right side of map), draw a zone
- **Show:** The Overture download modal with layer checkboxes and zone preview
- **Tip:** This shows the data pipeline UI — good for demonstrating the Overture Maps integration

---

## After Capturing

1. Place all PNGs in `docs/images/`
2. Verify they render: `git add docs/images/ && git status`
3. Open `README.md` in a Markdown previewer to confirm images display
4. Commit: `git add README.md docs/ && git commit -m "docs: add README with UI screenshots"`
