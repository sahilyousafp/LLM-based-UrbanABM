const moodColors: Record<string, string> = {
  happy:       '#30d158',
  excited:     '#ff9f0a',
  neutral:     '#5e5ce6',
  tired:       '#a78bfa',
  frustrated:  '#ff453a',
  curious:     '#64d2ff',
  anxious:     '#ff375f',
  sad:         '#4e9af1',
  calm:        '#00c7be',
  content:     '#34c759',
  bored:       '#8e8e93',
  energetic:   '#ffd60a',
  melancholy:  '#bf5af2',
  hopeful:     '#30b0c7',
  angry:       '#ff3b30',
  confident:   '#ff9500',
  reflective:  '#6e7bf5',
  overwhelmed: '#d94f70',
};

export function getMoodColor(mood: string): string {
  if (moodColors[mood]) return moodColors[mood];
  // Deterministic hue from string so every novel mood gets a unique, consistent colour.
  let h = 0;
  for (let i = 0; i < mood.length; i++) h = (h * 31 + mood.charCodeAt(i)) & 0xffff;
  return `hsl(${Math.round((h * 137.508) % 360)}, 65%, 55%)`;
}
