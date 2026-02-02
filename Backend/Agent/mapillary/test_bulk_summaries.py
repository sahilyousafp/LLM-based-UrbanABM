"""
Test script for the bulk agent summaries endpoint
Tests fetching all agent summaries simultaneously
"""

import requests
import time
from datetime import datetime

def test_bulk_summaries():
    """Test the /api/agents/summaries endpoint"""
    
    api_url = "http://127.0.0.1:8000"
    endpoint = f"{api_url}/api/agents/summaries"
    
    print("=" * 70)
    print("Testing Bulk Agent Summaries Endpoint")
    print("=" * 70)
    print(f"Endpoint: {endpoint}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 70)
    
    # Measure request time
    start_time = time.time()
    
    try:
        response = requests.get(endpoint, timeout=60)
        
        elapsed_time = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            
            print(f"✓ Success! Status Code: {response.status_code}")
            print(f"✓ Response Time: {elapsed_time:.2f} seconds")
            print("-" * 70)
            
            total_agents = data.get("total_agents", 0)
            summaries = data.get("summaries", [])
            
            print(f"Total Agents: {total_agents}")
            print(f"Summaries Received: {len(summaries)}")
            print("-" * 70)
            
            if summaries:
                print("\nFirst 3 Agent Summaries:")
                print("=" * 70)
                
                for i, summary in enumerate(summaries[:3], 1):
                    print(f"\n{i}. Agent {summary['agent_id']}")
                    print(f"   Location: ({summary['location']['lon']:.5f}, {summary['location']['lat']:.5f})")
                    print(f"   Nearby Amenities: {summary['amenity_count']}")
                    print(f"   Summary: {summary['summary'][:100]}...")
                
                print("\n" + "=" * 70)
                
                # Calculate statistics
                amenity_counts = [s['amenity_count'] for s in summaries]
                avg_amenities = sum(amenity_counts) / len(amenity_counts)
                max_amenities = max(amenity_counts)
                min_amenities = min(amenity_counts)
                
                print("\nStatistics:")
                print(f"  Average amenities per agent: {avg_amenities:.2f}")
                print(f"  Max amenities: {max_amenities}")
                print(f"  Min amenities: {min_amenities}")
                
                # Find agent with most amenities
                max_agent = max(summaries, key=lambda x: x['amenity_count'])
                print(f"\n  Agent with most amenities: Agent {max_agent['agent_id']} ({max_agent['amenity_count']} amenities)")
                print(f"  Location: ({max_agent['location']['lon']:.5f}, {max_agent['location']['lat']:.5f})")
                
        else:
            print(f"✗ Error! Status Code: {response.status_code}")
            print(f"Response: {response.text}")
            
    except requests.Timeout:
        print("✗ Request timed out (>60 seconds)")
        print("Note: This endpoint processes all agents, which may take time.")
    except requests.ConnectionError:
        print("✗ Connection Error")
        print("Make sure the backend server is running at http://127.0.0.1:8000")
    except Exception as e:
        print(f"✗ Unexpected Error: {e}")
    
    print("\n" + "=" * 70)
    print("Test Complete!")
    print("=" * 70)

if __name__ == "__main__":
    test_bulk_summaries()
