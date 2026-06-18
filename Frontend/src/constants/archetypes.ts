export const ARCHETYPE_COLORS: Record<string, string> = {
  resident: '#30d158',
  commuter: '#0a84ff',
  tourist:  '#ff9f0a',
  student:  '#ff375f',
};

// Nav-arrow colours — distinct from the 3D rim colours and from the orange trail (#ffa337).
export const ARCHETYPE_NAV_COLORS: Record<string, string> = {
  resident: '#32ade6',
  commuter: '#5e5ce6',
  tourist:  '#bf5af2',
  student:  '#ff375f',
};

export const ARCHETYPE_GLB: Record<string, string> = {
  resident: 'assets/agents/agent_res_low_512px.glb',
  commuter: 'assets/agents/agent_com_low_512px.glb',
  tourist:  'assets/agents/agent_tou_Female.glb',
  student:  'assets/agents/agent_stu_low_512px.glb',
};

export const GENERIC_GLB = 'assets/agents/agent_gen_low_512px.glb';

export const ARCHETYPE_DESCRIPTIONS: Record<string, string> = {
  resident:  'Familiar routes, local amenities. Driven by daily needs and habit.',
  commuter:  'Efficient and direct. Time-conscious with maximum path adherence.',
  tourist:   'Exploratory and curious. Seeks new streets, landmarks and views.',
  student:   'Budget-conscious and social. Balances study, leisure and food.',
};

export const ARCHETYPE_NAV_DEFAULTS: Record<string, string> = {
  resident: 'both',
  commuter: 'gps',
  tourist:  'direction_sense',
  student:  'both',
};

export const ARCHETYPES = ['resident', 'commuter', 'tourist', 'student'] as const;
export type BuiltinArchetype = typeof ARCHETYPES[number];
