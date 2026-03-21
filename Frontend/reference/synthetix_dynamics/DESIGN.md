# Design System Specification: The Kinetic Observatorium

## 1. Overview & Creative North Star

### Creative North Star: "The Kinetic Observatorium"
This design system is not a static interface; it is a high-precision lens for observing complex, emergent behaviors. While most data platforms feel like spreadsheets, this system adopts an **Editorial Tech** aesthetic. It blends the structural rigor of a scientific laboratory with the sophisticated, airy layout of a high-end architectural journal.

To move beyond the "template" look, we embrace **Intentional Asymmetry**. Large-scale typography is often offset against dense data clusters, and UI modules use varying widths to create a rhythmic, non-linear flow. We do not use borders to define space; we use light, depth, and tonal shifts to guide the eye through the simulation's complexity.

---

## 2. Colors & Surface Logic

The color palette is anchored in a deep, obsidian-like foundation (`#0f1419`), allowing vibrant agent paths and data points to achieve maximum luminosity.

### The "No-Line" Rule
**Strict Mandate:** Designers are prohibited from using 1px solid borders for sectioning or containment. 
Boundaries must be defined through:
- **Background Shifts:** Placing a `surface_container_low` (`#171c21`) element against the base `surface` (`#0f1419`).
- **Tonal Transitions:** Using the `surface_container` tiers to imply hierarchy.
- **Negative Space:** Leveraging the spacing scale (e.g., `spacing-10` or `spacing-12`) to create "invisible" gutters.

### Surface Hierarchy & Nesting
Treat the UI as a series of physical layers. 
- **The Simulation Canvas:** Always sits at `surface_container_lowest` (`#0a0f14`).
- **Global Navigation/Sidebar:** Sits at `surface_dim` (`#0f1419`).
- **Contextual Panels:** Nested modules should escalate through `surface_container` to `surface_container_highest` (`#30353b`) as they move closer to the user’s focus.

### The "Glass & Gradient" Rule
To elevate the "high-tech" feel, floating simulation controls must utilize **Glassmorphism**. 
- **Fill:** `surface_variant` (`#30353b`) at 60% opacity.
- **Effect:** `backdrop-filter: blur(12px)`.
- **Gradients:** Use subtle linear gradients for primary CTAs, transitioning from `primary_fixed_dim` (`#4cd6ff`) to `primary_container` (`#7bddff`) at a 135-degree angle. This adds "visual soul" that flat colors cannot replicate.

---

## 3. Typography: The Technical Editorial

The system pairs the geometric precision of **Space Grotesk** with the neutral, high-legibility of **Inter**.

- **Display & Headlines (Space Grotesk):** These are the "Editorial" voice. Use `display-lg` (3.5rem) and `headline-md` (1.75rem) for simulation titles and high-level metrics. The slight quirkiness of Space Grotesk signals a modern, high-tech identity.
- **Body & Labels (Inter):** For data density, Inter provides the "Technical" voice. `body-md` (0.875rem) is the workhorse for agent descriptions and logic parameters.
- **Information Hierarchy:** Always pair a `label-sm` (0.6875rem) in `on_surface_variant` (`#bac9cc`) with a `title-md` (1.125rem) in `on_surface` (`#dee3ea`) to create clear, scannable data pairs.

---

## 4. Elevation & Depth

We achieve depth through **Tonal Layering** rather than traditional drop shadows.

- **The Layering Principle:** Instead of a shadow, place a `surface_container_high` (`#252a30`) card on top of a `surface_container_low` (`#171c21`) section. This creates a soft, natural "lift."
- **Ambient Shadows:** When an element must float (e.g., a context menu), use an extra-diffused shadow: `box-shadow: 0 24px 48px rgba(0, 0, 0, 0.4)`. The shadow color should be a tinted version of `surface_container_lowest` to avoid a "muddy" appearance.
- **The "Ghost Border" Fallback:** If accessibility requires a stroke, use the `outline_variant` (`#3b494c`) at 20% opacity. It should feel like a suggestion of a line, not a hard barrier.

---

## 5. Components

### Primary Buttons
- **Shape:** `rounded-md` (0.75rem).
- **Surface:** Gradient of `primary_fixed_dim` to `primary_container`.
- **Text:** `label-md` in `on_primary` (`#003543`), all-caps with 0.05em letter spacing for an authoritative feel.

### Agent Path Chips
- **Aesthetic:** Minimalist, pill-shaped (`rounded-full`).
- **Color:** Use `secondary_container` (`#ffbf00`) for "Amber" paths and `tertiary_container` (`#d9c8ff`) for "Cyan/Violet" paths.
- **Interaction:** On hover, increase the opacity of the `on_secondary_container` text and add a subtle `primary` outer glow.

### Input Fields & Data Entry
- **Styling:** Forgo the "box" look. Use a `surface_container_highest` background with a `none` border. 
- **State:** On focus, the bottom edge gains a 2px "indicator" using `primary_fixed_dim`. 
- **Error:** Use the `error` token (`#ffb4ab`) only for the indicator line and the `body-sm` helper text.

### Simulation Cards & Lists
- **Prohibition:** **No dividers allowed.**
- **Separation:** Use `spacing-5` (1.1rem) of vertical white space or shift the background from `surface_container_low` to `surface_container`. This ensures the data feels integrated into the environment rather than trapped in a grid.

### New Component: The "Emergence Feed"
A vertical stream of simulation events. Use `surface_container_lowest` for the background, but highlight critical "Emergence" events with a `backdrop-blur` glass module that overlaps the feed's edge, creating that signature asymmetrical, editorial feel.

---

## 6. Do’s and Don’ts

### Do:
- **Use "Breathing Room":** If a section feels cramped, jump two levels on the spacing scale (e.g., move from `spacing-4` to `spacing-8`).
- **Embrace Overlaps:** Allow floating UI panels to slightly overlap the edge of the simulation canvas to create a sense of depth.
- **Color with Intent:** Use the `secondary` (Amber) tokens *only* for anomalies or critical path deviations. Use `primary` (Cyan) for standard agent behavior.

### Don’t:
- **Don't use pure black (#000000):** It kills the "glass" effect. Always use the `background` or `surface_container` tokens.
- **Don't use 100% opaque borders:** They clutter the technical visualization and make the platform feel like a legacy enterprise app.
- **Don't center-align editorial text:** Keep `display` and `headline` typography left-aligned to maintain the rigid, technical structure.