const moodColors: Record<string, string> = {
  excited:  '#ff9f0a',
  stressed: '#ff453a',
  bored:    '#8e8e93',
  relaxed:  '#30d158',
  neutral:  '#5e5ce6',
};

export function getMoodColor(mood: string): string {
  if (moodColors[mood]) return moodColors[mood];
  // Deterministic hue from string so every novel mood gets a unique, consistent colour.
  let h = 0;
  for (let i = 0; i < mood.length; i++) h = (h * 31 + mood.charCodeAt(i)) & 0xffff;
  return `hsl(${Math.round((h * 137.508) % 360)}, 65%, 55%)`;
}
