import requests
import math
from typing import List, Dict, Optional
from datetime import datetime

class MapillaryService:
    """
    Service to fetch street view images from Mapillary API
    
    Image Resolution:
    - Primary: 1024x768 (thumb_1024_url) - balanced quality and performance
    - Also fetches: 256px (thumbnail) and 2048px (high-res) for flexibility
    - Frontend always displays 1024px resolution via 'thumb_url' field
    """
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://graph.mapillary.com"
        self.headers = {
            "Authorization": f"OAuth {api_key}"
        }
        
    def get_images_near_location(self, lon: float, lat: float, radius: int = 50) -> List[Dict]:
        """
        Fetch Mapillary images near a specific location
        
        Args:
            lon: Longitude
            lat: Latitude
            radius: Search radius in meters (default: 50m)
            
        Returns:
            List of image data dictionaries with 'thumb_url' pointing to 1024px resolution
            
        Note:
            The 'thumb_url' field always contains thumb_1024_url (1024x768 resolution)
            for optimal balance between quality and loading performance.
            Alternative resolutions (256px, 2048px) are also provided as separate fields.
        """
        try:
            # Mapillary API endpoint for searching images
            url = f"{self.base_url}/images"
            
            params = {
                "access_token": self.api_key,
                "fields": "id,thumb_256_url,thumb_1024_url,thumb_2048_url,captured_at,compass_angle,geometry",
                "bbox": self._calculate_bbox(lon, lat, radius),
                "limit": 10  # Limit to 10 most recent images
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                images = data.get("data", [])
                
                # Format the response - thumb_url always uses 1024px resolution
                formatted_images = []
                for img in images:
                    formatted_images.append({
                        "id": img.get("id"),
                        "thumb_url": img.get("thumb_1024_url"),  # PRIMARY: Always 1024px resolution
                        "thumb_small": img.get("thumb_256_url"),   # 256px for thumbnails
                        "thumb_large": img.get("thumb_2048_url"),  # 2048px for high-res viewing
                        "captured_at": img.get("captured_at"),
                        "compass_angle": img.get("compass_angle"),
                        "coordinates": img.get("geometry", {}).get("coordinates", [lon, lat])
                    })
                
                return formatted_images
            else:
                print(f"Mapillary API error: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            print(f"Error fetching Mapillary images: {e}")
            return []
    
    def _calculate_bbox(self, lon: float, lat: float, radius: int) -> str:
        """
        Calculate bounding box around a point
        
        Args:
            lon: Center longitude
            lat: Center latitude
            radius: Radius in meters
            
        Returns:
            Bbox string in format "min_lon,min_lat,max_lon,max_lat"
        """
        # Approximate degrees per meter (at equator)
        # 1 degree latitude ≈ 111,320 meters
        # 1 degree longitude varies with latitude
        
        lat_offset = radius / 111320.0
        lon_offset = radius / (111320.0 * abs(math.cos(lat * math.pi / 180.0)))
        
        min_lon = lon - lon_offset
        min_lat = lat - lat_offset
        max_lon = lon + lon_offset
        max_lat = lat + lat_offset
        
        return f"{min_lon},{min_lat},{max_lon},{max_lat}"

