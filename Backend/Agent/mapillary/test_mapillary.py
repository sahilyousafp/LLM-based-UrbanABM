"""
Test script for Mapillary API integration
Tests fetching street view images for Barcelona's Eixample district
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from mapillary_service import MapillaryService

def test_mapillary():
    # Initialize service with API key
    api_key = "MLY|33533093396335529|30cb7c42be1a23189b63952f439551bd"
    service = MapillaryService(api_key)
    
    # Test location: Eixample, Barcelona (near Sagrada Familia)
    test_lon = 2.1734
    test_lat = 41.3951
    
    print(f"Testing Mapillary API...")
    print(f"Location: {test_lat}, {test_lon}")
    print(f"Searching for images within 50 meters...")
    print("-" * 60)
    
    # Fetch images
    images = service.get_images_near_location(test_lon, test_lat, radius=50)
    
    if images:
        print(f"✓ Success! Found {len(images)} street view images")
        print("-" * 60)
        
        for i, img in enumerate(images, 1):
            print(f"\nImage {i}:")
            print(f"  ID: {img['id']}")
            print(f"  Thumbnail URL (1024px): {img['thumb_url']}")
            print(f"  Captured: {img['captured_at']}")
            print(f"  Compass Angle: {img['compass_angle']}°")
            print(f"  Coordinates: {img['coordinates']}")
    else:
        print("✗ No images found or API error")
        print("This could mean:")
        print("  - No Mapillary coverage in this area")
        print("  - API key is invalid")
        print("  - Network connection issue")
    
    print("\n" + "=" * 60)
    print("Test complete!")

if __name__ == "__main__":
    test_mapillary()
