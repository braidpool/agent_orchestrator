#!/usr/bin/env python3
"""
Test script for error recovery system.
Simulates various failure scenarios to verify recovery mechanisms.
"""

import asyncio
import aiohttp
import json
import random
from typing import Dict, Any, List

class ErrorRecoveryTester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.session.close()
    
    async def test_query(self, query: str, test_name: str) -> Dict[str, Any]:
        """Send a test query and analyze the response"""
        print(f"\n{'='*60}")
        print(f"Test: {test_name}")
        print(f"Query: {query}")
        print("-" * 60)
        
        try:
            async with self.session.post(
                f"{self.base_url}/api/query",
                json={"query": query},
                timeout=aiohttp.ClientTimeout(total=120)
            ) as response:
                result = await response.json()

                # Analyze response
                self._analyze_response(result)

                # Basic validation
                assert "answer" in result, "Response missing 'answer' field"

                return result
                
        except Exception as e:
            print(f"Request failed: {e}")
            return {"error": str(e)}
    
    def _analyze_response(self, response: Dict[str, Any]):
        """Analyze and display response metadata"""
        
        # Check if partial result
        metadata = response.get("_metadata", {})
        if metadata:
            quality = metadata.get("quality_level", "unknown")
            successful = metadata.get("successful_agents", [])
            failed = metadata.get("failed_agents", [])
            
            print(f"Quality Level: {quality}")
            print(f"Successful Agents: {', '.join(successful)}")
            
            if failed:
                print("\nFailed Agents:")
                for failure in failed:
                    print(f"  - {failure['agent']}: {failure['error']}")
                    print(f"    Impact: {failure['impact']}")
        
        # Check confidence
        confidence = response.get("confidence_score", 0)
        print(f"\nConfidence Score: {confidence:.2f}")
        
        # Check if from cache
        if response.get("from_cache"):
            print("Result from cache")
        
        # Show answer preview
        answer = response.get("answer", "No answer")
        preview = answer[:200] + "..." if len(answer) > 200 else answer
        print(f"\nAnswer Preview: {preview}")
    
    async def simulate_network_issues(self):
        """Test queries that might cause network issues"""
        queries = [
            # Normal query for baseline
            ("What is Python programming?", "Baseline - Should succeed"),
            
            # Complex query that might timeout
            ("Analyze the complete history of quantum computing, all major breakthroughs, "
             "current applications, future prospects, and compare with classical computing",
             "Complex query - Might cause timeouts"),
            
            # Query with special characters
            ("What is the meaning of 🤖 and how does AI work?", 
             "Special characters - Test encoding"),
            
            # Very short query
            ("AI", "Minimal query - Test basic fallbacks"),
            
            # Query that might not return results
            ("xyzabc123 quantum flibbertigibbet nonsense query",
             "Nonsense query - Test no results handling")
        ]
        
        for query, test_name in queries:
            await self.test_query(query, test_name)
            await asyncio.sleep(2)  # Delay between tests
    
    async def test_circuit_breaker(self):
        """Test circuit breaker by sending many requests"""
        print("\n" + "="*60)
        print("CIRCUIT BREAKER TEST")
        print("Sending 10 rapid requests to trigger circuit breaker")
        print("="*60)
        
        tasks = []
        for i in range(10):
            query = f"Test circuit breaker query {i}"
            task = self.test_query(query, f"Circuit test {i}")
            tasks.append(task)
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Analyze circuit breaker behavior
        failures = sum(1 for r in results if isinstance(r, Exception) or 
                      (isinstance(r, dict) and r.get("error")))
        
        print(f"\nCircuit Breaker Summary:")
        print(f"Total requests: 10")
        print(f"Failed requests: {failures}")
    
    async def test_partial_results(self):
        """Test system behavior with partial agent failures"""
        # This query should work even if some agents fail
        queries = [
            "Tell me about artificial intelligence",
            "What are the latest news about space exploration?",
            "Explain machine learning in simple terms"
        ]
        
        print("\n" + "="*60)
        print("PARTIAL RESULTS TEST")
        print("Testing graceful degradation with agent failures")
        print("="*60)
        
        for query in queries:
            result = await self.test_query(query, "Partial results test")
            
            # Check if we got an answer despite failures
            if result.get("answer") and result.get("_metadata", {}).get("failed_agents"):
                print("\n✓ SUCCESS: Got answer despite agent failures!")
            elif result.get("answer"):
                print("\n✓ SUCCESS: All agents worked")
            else:
                print("\n✗ FAILURE: No answer provided")
    
    async def check_monitoring(self):
        """Check system monitoring endpoint"""
        print("\n" + "="*60)
        print("SYSTEM MONITORING")
        print("="*60)
        
        try:
            async with self.session.get(f"{self.base_url}/api/monitoring") as response:
                data = await response.json()
                
                # Show agent status
                print("\nState Manager:")
                sm = data["state_manager"]
                print(f"  Queue size: {sm['queue_size']}")
                print(f"  Pending writes: {sm['pending_writes']}")
                print(f"  Writer running: {sm['writer_running']}")
                
                print("\nActive Jobs:")
                print(f"  Count: {data['active_jobs']['count']}")
                
                # Check for any issues
                if sm['queue_size'] > 50:
                    print("\n⚠️  WARNING: Large queue size detected")
                    
        except Exception as e:
            print(f"Failed to get monitoring data: {e}")

async def main():
    """Run all error recovery tests"""
    print("ERROR RECOVERY SYSTEM TEST")
    print("="*60)
    print("This test will simulate various failure scenarios")
    print("to verify the error recovery system works correctly.")
    print("="*60)
    
    async with ErrorRecoveryTester() as tester:
        # Check if server is running
        try:
            async with tester.session.get(f"{tester.base_url}/api/health") as resp:
                if resp.status != 200:
                    print("Error: Server not healthy")
                    return
        except:
            print("Error: Server not running on http://localhost:8000")
            print("Please start the server first with: python main.py")
            return
        
        # Run tests
        print("\n1. Testing various query scenarios...")
        await tester.simulate_network_issues()
        
        print("\n2. Testing partial results handling...")
        await tester.test_partial_results()
        
        print("\n3. Checking system monitoring...")
        await tester.check_monitoring()
        
        # Note: Circuit breaker test might overwhelm the system
        # Uncomment to test:
        # print("\n4. Testing circuit breaker...")
        # await tester.test_circuit_breaker()
        
        print("\n" + "="*60)
        print("TEST COMPLETE")
        print("Check logs for detailed error recovery behavior:")
        print("  grep -i 'retry\\|fallback\\|circuit' logs/*.log")
        print("="*60)

if __name__ == "__main__":
    asyncio.run(main())
