#!/usr/bin/env python3
"""
Test script for OSM Update Service
Tests all endpoints to ensure they work correctly
"""

import requests
import time
import sys

BASE_URL = "http://localhost:8001"

def test_endpoint(name, method, endpoint, data=None):
    """Test a single endpoint"""
    url = f"{BASE_URL}{endpoint}"
    print(f"\n{'='*60}")
    print(f"Testing: {name}")
    print(f"Method: {method} {endpoint}")
    print('='*60)
    
    try:
        if method == "GET":
            response = requests.get(url, timeout=5)
        elif method == "POST":
            response = requests.post(url, json=data, timeout=5)
        else:
            print(f"❌ Unknown method: {method}")
            return False
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ SUCCESS")
            print("\nResponse:")
            print(response.json())
            return True
        else:
            print(f"❌ FAILED")
            print(f"Response: {response.text}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ FAILED: Could not connect to service")
        print("   Make sure the service is running on port 8001")
        return False
    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False

def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("OSM Update Service Test Suite")
    print("="*60)
    
    # Check if service is running
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=2)
        if response.status_code != 200:
            print("\n❌ Service is not responding correctly")
            print("   Start the service with: python osm_update_service.py")
            sys.exit(1)
    except:
        print("\n❌ Service is not running")
        print("   Start the service with: python osm_update_service.py")
        sys.exit(1)
    
    print("\n✅ Service is running")
    
    # Run tests
    results = []
    
    results.append(test_endpoint(
        "Root Endpoint",
        "GET",
        "/"
    ))
    
    results.append(test_endpoint(
        "Health Check",
        "GET",
        "/health"
    ))
    
    results.append(test_endpoint(
        "Status Check",
        "GET",
        "/status"
    ))
    
    results.append(test_endpoint(
        "Database Statistics",
        "GET",
        "/db/stats"
    ))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(results)
    total = len(results)
    
    print(f"\nPassed: {passed}/{total}")
    
    if passed == total:
        print("\n✅ All tests passed!")
        return 0
    else:
        print(f"\n❌ {total - passed} test(s) failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
