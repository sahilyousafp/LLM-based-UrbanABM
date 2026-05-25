import pandas as pd, json, sys

path = sys.argv[1] if len(sys.argv) > 1 else "test/tracking_data/2026-05-24/tourist/both/20260524_233116_both.parquet"
df = pd.read_parquet(path)

print("=== OVERVIEW ===")
print(f"Steps: {len(df)}  |  Archetype: {df['archetype'].iloc[0]}  |  Perception mode: {df['perception_mode'].iloc[0]}")
if "target_name" in df.columns:
    print(f"Target: {df['target_name'].iloc[0]}  ({df.get('target_amenity_type', ['?']).iloc[0]})")
print()

print("=== PATH ADHERENCE ===")
if "on_proposed_path" in df.columns:
    on_path = df["on_proposed_path"].value_counts().to_dict()
    pct = df["on_proposed_path"].mean() * 100
    print(f"on_proposed_path: {on_path}  -> {pct:.1f}% on-path")
else:
    print("on_proposed_path: not recorded (older format)")
fb = df["is_fallback"].value_counts().to_dict()
print(f"is_fallback:      {fb}")
pa = df["perception_available"].value_counts().to_dict()
print(f"perception_avail: {pa}  -> {df['perception_available'].mean()*100:.1f}% had perception")
print()

print("=== NEEDS (start vs end) ===")
def parse_needs(row):
    try:
        n = json.loads(row) if isinstance(row, str) else row
        return n if isinstance(n, dict) else {}
    except Exception:
        return {}

needs_start = parse_needs(df["needs_json"].iloc[0])
needs_end   = parse_needs(df["needs_json"].iloc[-1])
print(f"Start: {{{', '.join(f'{k}: {v:.3f}' for k,v in needs_start.items())}}}")
print(f"End:   {{{', '.join(f'{k}: {v:.3f}' for k,v in needs_end.items())}}}")
print()

print("=== COGNITION (start vs end) ===")
cog_start = parse_needs(df["cognition_state_json"].iloc[0])
cog_end   = parse_needs(df["cognition_state_json"].iloc[-1])
print(f"Start: {cog_start}")
print(f"End:   {cog_end}")
print()

print("=== THOUGHT STREAM — last 10 mobility events ===")
shown = 0
for _, row in df.iloc[::-1].iterrows():
    if shown >= 10:
        break
    ts = row.get("thought_stream_json")
    if not ts:
        continue
    try:
        events = json.loads(ts) if isinstance(ts, str) else ts
    except Exception:
        continue
    mob = [e for e in events if e.get("topic") == "mobility"]
    if not mob:
        continue
    e = mob[-1]
    meta = e.get("metadata", {})
    desc = e.get("description", "")
    r_idx = desc.find("Reason:")
    reason = desc[r_idx + 7:r_idx + 130].strip() if r_idx >= 0 else desc[:120]
    on_p = meta.get("on_path", "?")
    src  = meta.get("data_sources", "?")
    fb_m = meta.get("fallback", "?")
    print(f"  step={row['step']}  on_path={on_p}  src={src}  fallback={fb_m}")
    print(f"    {reason}")
    shown += 1

print()
print("=== DECISION REASONS — last 5 steps ===")
for r in df["decision_reason"].dropna().tail(5):
    print(f"  - {str(r)[:150]}")
