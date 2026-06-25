export const LOCAL_PROVIDERS = [
  { id: 'ollama',   name: 'Ollama',   desc: 'Local LLM — no GPU required.' },
  { id: 'vllm',     name: 'vLLM',     desc: 'GPU-backed Docker container.' },
  { id: 'lmdeploy', name: 'LMDeploy', desc: 'Optimised GPU inference.' },
  { id: 'docker',   name: 'Docker',   desc: 'llama.cpp via Docker Runner.' },
];

export const API_PROVIDERS = [
  { id: 'gemini',     name: 'Google Gemini', desc: 'Flash 2.0 / Pro — best price/quality.' },
  { id: 'openai',     name: 'OpenAI',        desc: 'GPT-4o / o-mini — premium model.' },
  { id: 'deepseek',   name: 'DeepSeek',      desc: 'V3 / Chat — strong reasoning, low cost.' },
  { id: 'openrouter', name: 'OpenRouter',    desc: 'Multi-model gateway.' },
  { id: 'groq',       name: 'Groq',          desc: 'Ultra-fast Llama / Mixtral.' },
];

export const DEFAULT_MODELS: Record<string, string> = {
  gemini:     'gemini-2.0-flash-lite',
  openai:     'gpt-4o-mini',
  deepseek:   'deepseek-chat',
  openrouter: 'openai/gpt-4o-mini',
  groq:       'llama-3.3-70b-versatile',
  ollama:     'qwen2.5-coder:3b',
  vllm:       'Qwen/Qwen2.5-7B-Instruct',
  lmdeploy:   'qwen2.5-coder:3b',
  docker:     'llama3.2:3b',
};

export interface ABMScore { spatial: number; fidelity: number; json: number; speed: number; cost: number; eqbench: number; }

export const ABM_SCORES: Record<string, ABMScore> = {
  gemini:     { spatial: 4, fidelity: 4, json: 5, speed: 5, cost: 5, eqbench: 72 },
  openai:     { spatial: 4, fidelity: 5, json: 5, speed: 4, cost: 2, eqbench: 79 },
  deepseek:   { spatial: 4, fidelity: 4, json: 4, speed: 3, cost: 5, eqbench: 83 },
  openrouter: { spatial: 3, fidelity: 4, json: 4, speed: 3, cost: 3, eqbench: 79 },
  groq:       { spatial: 3, fidelity: 3, json: 4, speed: 5, cost: 5, eqbench: 72 },
  ollama:     { spatial: 2, fidelity: 3, json: 3, speed: 2, cost: 5, eqbench: 55 },
  vllm:       { spatial: 4, fidelity: 4, json: 5, speed: 4, cost: 5, eqbench: 60 },
  lmdeploy:   { spatial: 4, fidelity: 4, json: 5, speed: 5, cost: 5, eqbench: 55 },
  docker:     { spatial: 2, fidelity: 2, json: 3, speed: 2, cost: 5, eqbench: 48 },
};

export const PROVIDER_COLORS: Record<string, string> = {
  ollama: '#bf5af2', vllm: '#a29bfe', lmdeploy: '#6c5ce7', docker: '#636e72',
  gemini: '#0a84ff', openai: '#30d158', deepseek: '#ff9f0a', openrouter: '#ff453a', groq: '#4ecdc4',
};
export const CUSTOM_COLORS = ['#ff6b6b', '#45b7d1', '#f9ca24', '#fd79a8', '#e17055', '#00cec9'];

export interface ABMDim { key: keyof ABMScore; label: string; tip: string; max?: number; }

export const ABM_DIMS: ABMDim[] = [
  { key: 'spatial',  label: 'Urban',  tip: 'Street-level spatial reasoning' },
  { key: 'fidelity', label: 'Role',   tip: 'Persona consistency across steps' },
  { key: 'json',     label: 'JSON',   tip: 'Structured output reliability' },
  { key: 'speed',    label: 'Speed',  tip: 'Inference latency suitability' },
  { key: 'cost',     label: 'Cost',   tip: 'Operational cost efficiency' },
  { key: 'eqbench',  label: 'EQ',     tip: 'EQ-Bench V2 emotional intelligence (Paech, 2024)', max: 100 },
];
