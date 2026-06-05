# Frontend Redesign for Universal singularity

I want you to be an expert frontend designer with immense capability for UX and UI for users of my platform and as a validation base for the technology and architecture of the platform. The UI is designed for simpler users with a deviation towards apple's design philosophy for translucent floating panels. Replace the file in the root frontend folder.

## The system order (In panels)

1. Zone selection for VLM streetview download.
    - Enable the user to view the map with MapBox API and then select a region to download the streetview images.
    - The images are fetched from google streetview API
    - The system checks if there are existing images that are already fetched and doesnt downlaod them again.
    - The user has the option to select the spacing of the images to be downlaoded in the UI.
    - After the download the images are displayed on the map as points.
    - The existing images will already be shown in the UI and the user can click them. It will then pop-up on the side and show it to them to verify.
    - The user can also edit those images to their preference (Brightness, contrast and Saturation)
2. After clicking next, it switches to the next panel for VLM analysis.
    - Here the user will be shown the parameters that the VLM will extract from the images to then convert it into prompts.
    - Use the current text context to show them on the UI as editable text boxes for inputs
    - The user can also deselect the parameters that doesnt need to be analysed or even add parameters in the UI itself.
    - On another panel it  shows the list of VLMs they can use (show the current VLMs available with the pros and cons of using each of them).  The user can also click on custom to input a huggingface link to fetch and try their own VLM. If they use their own VLM, have a compare button to compare the with the existing preffered VLMs.
    - Then clicking analyse will download the analysis for those points and when each point is downloaded it changes colours. The user can also click a point or a bunch of points and then click analyse for a selection of images also.
3. Personality Adjustment.
    - The next panel is about creating agents and their personalities.
    - The user is first given 4 preset archetypes as starting point and these agents are shown as 3d agents overlayed over the background map. These 3d models of the agents will be rotating slowly with their archetype written below.
    - there will be a + button to create your own archetype.
    - Besides that, when they click each agent, the plans.json ie; the information about the personal will be show as fillable text boxes with sliders for fields that are required. It includes their preferences, moods, cognition, etc..
    - Once done, they move to the single agent analysis.

    ### 3D Character Assets (GLB models)
    The four preset archetypes and any custom archetype each use a dedicated GLB model rendered in Three.js r161:

    | Archetype | GLB file            | Colour accent |
    |-----------|---------------------|---------------|
    | Resident  | `agent_res.glb`     | `#30d158` (green)  |
    | Commuter  | `agent_com.glb`     | `#0a84ff` (blue)   |
    | Tourist   | `agent_tou.glb`     | `#ff9f0a` (amber)  |
    | Student   | `agent_stu.glb`     | `#ff375f` (red)    |
    | Custom    | `agent_gen.glb`     | `#5e5ce6` (indigo) |

    Source files live in `Frontend/assets/agents/`. They are served by the map-server backend
    (`GET /api/assets/agents/{filename}`) so they load correctly regardless of whether the
    HTML is opened as a `file://` URL or served via HTTP.

    Each model is auto-scaled to a ~1.8-unit bounding box by the Three.js `GLTFLoader` callback,
    grounded at y = 0, and centred on the x/z axes. A rim `PointLight` tinted with the archetype's
    accent colour gives the figures a distinctive glow.
4. Single Agent analysis.
    - This is where we incorporate the test system.
    - Here the user will have the ability to simulate the single agents from the list of agents created before.
    - They also have the option to choose the LLM engine they prefer with the tabs separating local testing/ API based testing.
    - If they choose API, they will have a settings option to input the API key and the model category (for gemini - flash/2.5/3 etc.) Just like the VLM, the system showcases the comparison between the models with respect to human emotional accuracy, API costing and speed.
    - They also have the option to choose if they want direction awareness or GPS along with the activation distance for each of them.
    - While the simulation is running the users see the metrics on the right with the pie charts for the percentage of emotions, the thought stream, what the agent sees, etc.
    - The user can also see the path and it highlights points on the path showcasing the changes in direction, and various layers from the visualisation jupyter notebook that enables the user to evaluate the agent.
    - Once the simulation is done or stopped, there is a button to see the results as a whole, with pie charts and thought streams, behavioral changes, emotional changes that are interractive on a floating panel over the Ui. These values can be represented as points or heatmaps.
5. Multi Agent
    - The final panel has multi agent simulation with the same concept as the single agent but with more agents that the user prescribes, along with the LLM model prescription as before.
    - The user can also pick where the agents are spawn, with clicking points, random with a selected zone, start from POIs, etc.
    - The multi agent simulation will also incorporate homes for the residential agent or work spaces for the commuter agent.
    - The interface will have the same information as the single agent with metrics, LLM choice and such.

Make sure the UI and end points are accurate and works well. The system shoud be clean and production ready with a single batch file excecution.