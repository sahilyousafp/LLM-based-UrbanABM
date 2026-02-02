# Mapillary Image Resolution Configuration

## Current Configuration

**Active Resolution: 1024x768 pixels**

All street view images displayed in the frontend use the **1024px resolution** (`thumb_1024_url` from Mapillary API).

---

## Why 1024px?

### Optimal Balance
- ✅ **Quality**: Sharp, detailed images suitable for viewing
- ✅ **Performance**: Fast loading (~150-300 KB per image)
- ✅ **Bandwidth**: Reasonable data usage for real-time updates
- ✅ **UX**: No noticeable lag when fetching multiple images

### Comparison

| Resolution | File Size* | Load Time** | Use Case |
|------------|-----------|-------------|----------|
| 256px | ~30-50 KB | < 0.1s | Thumbnails, low bandwidth |
| **1024px** | **~150-300 KB** | **< 0.5s** | **Main display (CURRENT)** |
| 2048px | ~500-800 KB | 1-2s | High-res viewing, zoom |

*Approximate, varies by image content  
**On typical broadband connection

---

## Available Resolutions

The Mapillary API provides three resolution options:

### 1. thumb_256_url (256px)
- **Size**: 256px wide
- **Use**: Small thumbnails, preview grids
- **Pros**: Tiny file size, instant loading
- **Cons**: Low detail, pixelated when enlarged

### 2. thumb_1024_url (1024px) ⭐ CURRENT
- **Size**: 1024px wide
- **Use**: Primary display, main viewing
- **Pros**: Good quality, fast loading, balanced
- **Cons**: None for typical use

### 3. thumb_2048_url (2048px)
- **Size**: 2048px wide
- **Use**: High-res viewing, print quality
- **Pros**: Excellent quality, zooming
- **Cons**: Larger files, slower loading

---

## Implementation

### Backend: mapillary_service.py

```python
# Line 53: Primary mapping always uses 1024px
formatted_images.append({
    "id": img.get("id"),
    "thumb_url": img.get("thumb_1024_url"),  # PRIMARY: 1024px
    "thumb_small": img.get("thumb_256_url"),   # Alternative: 256px
    "thumb_large": img.get("thumb_2048_url"),  # Alternative: 2048px
    "captured_at": img.get("captured_at"),
    "compass_angle": img.get("compass_angle"),
    "coordinates": img.get("geometry", {}).get("coordinates", [lon, lat])
})
```

### Frontend: index.html & mapbox.html

```javascript
// Uses 'thumb_url' which is mapped to thumb_1024_url
imgElement.src = img.thumb_url;  // Displays 1024px image
```

---

## How to Change Resolution

### To Use 256px (Lower Quality, Faster)

**File**: `Backend/Agent/mapillary/mapillary_service.py`

```python
# Line 53: Change from thumb_1024_url to thumb_256_url
"thumb_url": img.get("thumb_256_url"),  # Now uses 256px
```

**Use Case**: Very slow connections, mobile data saving

---

### To Use 2048px (Higher Quality, Slower)

**File**: `Backend/Agent/mapillary/mapillary_service.py`

```python
# Line 53: Change from thumb_1024_url to thumb_2048_url
"thumb_url": img.get("thumb_2048_url"),  # Now uses 2048px
```

**Use Case**: High-quality displays, detailed analysis, large screens

---

### To Use Dynamic Resolution (Advanced)

Add logic to choose resolution based on context:

```python
# Example: Choose resolution based on viewport or network
def get_optimal_resolution(img, network_speed="fast"):
    if network_speed == "slow":
        return img.get("thumb_256_url")
    elif network_speed == "fast":
        return img.get("thumb_2048_url")
    else:
        return img.get("thumb_1024_url")  # Default

formatted_images.append({
    "thumb_url": get_optimal_resolution(img),
    # ... other fields
})
```

---

## API Request

The Mapillary API is always requested with all three resolutions:

```python
# mapillary_service.py line 37
params = {
    "access_token": self.api_key,
    "fields": "id,thumb_256_url,thumb_1024_url,thumb_2048_url,captured_at,compass_angle,geometry",
    "bbox": self._calculate_bbox(lon, lat, radius),
    "limit": 10
}
```

This ensures all resolutions are available, but only the one mapped to `thumb_url` is used by default.

---

## Performance Impact

### Current Setup (1024px)
- **Update interval**: 5 seconds
- **Images per update**: ~8-10 images
- **Bandwidth per update**: ~1.5-3 MB
- **Total per minute**: ~18-36 MB
- **Impact**: Minimal on modern connections

### If Using 2048px
- **Update interval**: 5 seconds
- **Images per update**: ~8-10 images
- **Bandwidth per update**: ~5-8 MB
- **Total per minute**: ~60-96 MB
- **Impact**: Significant on slower connections

### If Using 256px
- **Update interval**: 5 seconds
- **Images per update**: ~8-10 images
- **Bandwidth per update**: ~0.3-0.5 MB
- **Total per minute**: ~3.6-6 MB
- **Impact**: Negligible

---

## Recommendations

### Use 256px When:
- Testing on mobile devices
- Limited bandwidth environments
- Many simultaneous users
- Grid view with many small images

### Use 1024px When: ⭐ RECOMMENDED
- Normal desktop viewing
- Standard displays
- Real-time monitoring
- Balanced quality and performance needed

### Use 2048px When:
- Large displays (>27 inches)
- Detailed analysis required
- Print quality needed
- Single-image focused viewing

---

## Frontend Display

Both frontends use the same resolution seamlessly:

### index.html (Leaflet)
```javascript
// Line 581
<img src="${img.thumb_url}" 
     alt="Street view captured on ${new Date(img.captured_at).toLocaleDateString()}"
     loading="lazy">
```

### mapbox.html (Mapbox)
```javascript
// Line 1214
imgElement.src = img.thumb_url;
imgElement.alt = `Street view captured on ${new Date(img.captured_at).toLocaleDateString()}`;
imgElement.loading = 'lazy';
```

The `loading="lazy"` attribute ensures images are only loaded when needed, improving performance regardless of resolution.

---

## Testing Different Resolutions

To test different resolutions without changing code:

```bash
# Test current setup (1024px)
cd Backend\Agent\mapillary
python test_mapillary.py

# Output will show: "Thumbnail URL (1024px): https://..."
```

You can also inspect the actual URLs returned to verify resolution:
- `thumb_256_url` contains `/s256x192/` in URL
- `thumb_1024_url` contains `/s1024x768/` in URL
- `thumb_2048_url` contains `/s2048x1536/` in URL

---

## Browser DevTools Inspection

To verify resolution in browser:
1. Open browser DevTools (F12)
2. Go to Network tab
3. Filter by "Img"
4. Click an agent to load street views
5. Check image file sizes:
   - 256px: ~30-50 KB
   - 1024px: ~150-300 KB (should see this)
   - 2048px: ~500-800 KB

---

## Frequently Asked Questions

**Q: Why not always use highest resolution?**  
A: 2048px images are 3-5x larger, causing slower loading and higher bandwidth usage with minimal visual improvement on standard displays.

**Q: Can I use different resolutions for different views?**  
A: Yes, you can access `thumb_small` or `thumb_large` in the response and use them conditionally in the frontend.

**Q: Does this affect mobile performance?**  
A: 1024px is fine for mobile. For mobile-only optimization, consider detecting device and using 256px on small screens.

**Q: Can users choose resolution?**  
A: Not currently implemented, but you could add a settings toggle in the UI that switches between thumb_small, thumb_url, and thumb_large.

---

## Future Enhancements

- **Adaptive resolution**: Auto-detect network speed and adjust
- **Progressive loading**: Load 256px first, then upgrade to 1024px
- **User preference**: Let users choose quality setting
- **Responsive images**: Use different resolutions for different viewport sizes
- **Caching**: Cache 1024px images locally for instant re-display

---

**Last Updated**: 2026-02-02  
**Current Resolution**: 1024px (thumb_1024_url)  
**Status**: Optimized for balanced performance
