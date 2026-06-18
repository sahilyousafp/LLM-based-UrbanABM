import type { PerceptionFieldSpec, PerceptionSubField, VLMCardSpec } from '../types';

export const PERCEPTION_FIELDS: PerceptionFieldSpec[] = [
  { key: 'scene',             label: 'Scene overview',    prompt: 'One sentence describing the overall urban scene.' },
  { key: 'lighting',          label: 'Lighting',          prompt: 'Per-zone lighting: {zone, element, condition: dark|dim|adequate|bright}.' },
  { key: 'spatial_character', label: 'Spatial character', prompt: 'Per-zone: {zone, width: narrow|moderate|wide, enclosure: open|semi|enclosed, passability: clear|obstructed, lane_type: sidewalk|road|shared|plaza, crossing: none|zebra|signalised}.' },
  { key: 'crowdedness',       label: 'Crowdedness',       prompt: 'Per-zone pedestrian density: {zone, density_level: empty|sparse|moderate|dense}.' },
  { key: 'greenery',          label: 'Greenery',          prompt: 'Per-zone vegetation: {zone, element, coverage: none|sparse|moderate|dense}.' },
  { key: 'street_amenities',  label: 'Street amenities',  prompt: 'Per-zone street furniture: {zone, element, material_and_colour, presence: none|few|several|many}.' },
  { key: 'visible_text',      label: 'Visible text',      prompt: 'Readable text per zone: {text, zone, type: sign|label|graffiti}.' },
];

export const FIELD_DEFAULT_SUBFIELDS: Record<string, PerceptionSubField[]> = {
  lighting:          [{ key: 'condition',           values: ['dark','dim','adequate','bright'] }],
  spatial_character: [{ key: 'width',               values: ['narrow','moderate','wide'] },
                      { key: 'enclosure',            values: ['open','semi','enclosed'] },
                      { key: 'passability',          values: ['clear','obstructed'] },
                      { key: 'lane_type',            values: ['sidewalk','road','shared','plaza'] },
                      { key: 'crossing',             values: ['none','zebra','signalised'] },
                      { key: 'architectural_style',  values: ['neo_gothic','modernist','contemporary','neoclassical','vernacular','eclectic','art_deco','other'] },
                      { key: 'building_condition',   values: ['excellent','good','fair','poor','under_construction'] },
                      { key: 'storefront_type',      values: ['retail','restaurant','cafe','office','residential','hotel','vacant','cultural','industrial','other'] },
                      { key: 'architectural_details', values: [] }],
  crowdedness:       [{ key: 'density_level',       values: ['empty','sparse','moderate','dense'] }],
  greenery:          [{ key: 'coverage',             values: ['none','sparse','moderate','dense'] }],
  street_amenities:  [{ key: 'material_colour',      values: [] },
                      { key: 'presence',             values: ['none','few','several','many'] }],
  visible_text:      [{ key: 'type',                 values: ['sign','label','graffiti'] }],
};

export const VLM_CARDS: VLMCardSpec[] = [
  { id: 'qwen25vl-3b',  name: 'Qwen2.5-VL 3B',         active: true,
    pros: 'Best speed/quality balance. Already cached for all 300+ points.',
    cons: 'Smaller context window than 7B variant.',
    props: { Latency: '~2.5s', Memory: '6 GB', License: 'Tongyi' } },
  { id: 'qwen25vl-7b',  name: 'Qwen2.5-VL 7B',
    pros: 'Stronger spatial reasoning and richer captions.',
    cons: 'Needs 12 GB VRAM; slower.',
    props: { Latency: '~6.0s', Memory: '12 GB', License: 'Tongyi' } },
  { id: 'llava-1.6-7b', name: 'LLaVA-1.6 7B',
    pros: 'Robust general-purpose VLM, well documented.',
    cons: 'Weaker structured-output adherence.',
    props: { Latency: '~5.5s', Memory: '11 GB', License: 'Apache' } },
  { id: 'pixtral-12b',  name: 'Pixtral 12B',
    pros: 'Top quality on complex urban scenes.',
    cons: 'Needs 24 GB VRAM; cloud cost.',
    props: { Latency: '~9.0s', Memory: '24 GB', License: 'Apache' } },
  { id: 'custom-hf',    name: '+ Custom HuggingFace',
    pros: 'Paste any HF repo_id (vision-language).',
    cons: 'Requires VLM runtime (v2).',
    props: { Latency: '—', Memory: '—', License: '—' } },
];
