#!/usr/bin/env python3
"""
Test script for graceful shutdown functionality.
Sends multiple requests then monitors shutdown process.
"""

import asyncio
import aiohttp
import json
import sys
import time

async def send_requests(num_requests=20):
    """Send multiple requests to test queue behavior"""
    print(f"Sending {num_requests} requests...")
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        
        for i in range(num_requests):
            query = f"Test query {i}: What is the meaning of life?"
            task = session.post(
                'http://localhost:8000/api/query',
                json={"query": query},
                timeout=aiohttp.ClientTimeout(total=60)
            )
            tasks.append(task)
            
            # Small delay to spread out requests
            if i % 5 == 0:
                await asyncio.sleep(0.1)
        
        # Send all requests
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        success = sum(1 for r in results if not isinstance(r, Exception))
        print(f"Sent {success}/{num_requests} requests successfully")

async def monitor_shutdown():
    """Monitor the shutdown process"""
    print("\nMonitoring server status...")
    print("Press Ctrl+C on the server to test graceful shutdown")
    print("-" * 50)
    
    async with aiohttp.ClientSession() as session:
        last_queue_size = 0
        
        while True:
            try:
                # Get monitoring data
                async with session.get('http://localhost:8000/api/monitoring') as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        sm = data['state_manager']
                        queue_size = sm['queue_size']
                        pending = sm['pending_writes']
                        shutting_down = sm['is_shutting_down']
                        active_jobs = data['active_jobs']['count']
                        
                        # Only print if something changed
                        if (queue_size != last_queue_size or 
                            pending > 0 or 
                            shutting_down or 
                            active_jobs > 0):
                            
                            status = "SHUTTING DOWN" if shutting_down else "RUNNING"
                            print(f"[{time.strftime('%H:%M:%S')}] "
                                  f"Status: {status} | "
                                  f"Queue: {queue_size} | "
                                  f"Pending: {pending} | "
                                  f"Active Jobs: {active_jobs}")
                            
                            last_queue_size = queue_size
                        
                        # If shutdown complete, exit
                        if shutting_down and queue_size == 0 and pending == 0:
                            print("\n✓ Graceful shutdown completed successfully!")
                            break
                            
            except aiohttp.ClientError:
                print("\n✗ Server stopped (connection refused)")
                break
            except Exception as e:
                print(f"\nError: {e}")
                break
            
            await asyncio.sleep(0.5)

async def main():
    """Main test function"""
    print("Graceful Shutdown Test")
    print("=" * 50)
    
    # Check if server is running
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('http://localhost:8000/api/health') as resp:
                if resp.status != 200:
                    print("Error: Server not responding properly")
                    return
    except:
        print("Error: Server not running on http://localhost:8000")
        print("Please start the server first with: python main.py")
        return
    
    # Send test requests
    await send_requests(20)
    
    # Monitor shutdown
    await monitor_shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted")