import asyncio
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from contextlib import asynccontextmanager
import logging
import signal
import sys

class StateManager:
    """
    Manages persistent state with graceful shutdown support.
    
    Features:
    - Single writer pattern to avoid database contention
    - Graceful shutdown with queue draining
    - Monitoring of queue status and pending writes
    - Timeout protection during shutdown
    """
    
    def __init__(self, db_path: str = "./state.db", shutdown_timeout: int = 30):
        self.db_path = Path(db_path)
        self.write_queue = asyncio.Queue()
        self.logger = logging.getLogger("StateManager")
        self._init_db()
        
        # Shutdown handling
        self._shutdown_event = asyncio.Event()
        self._writer_task = None
        self._pending_writes = 0
        self._shutdown_timeout = shutdown_timeout  # seconds
        
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    result TEXT NOT NULL,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_status (
                    agent TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    last_success TIMESTAMP,
                    consecutive_failures INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
    
    async def start(self):
        """Start the write worker"""
        self._writer_task = asyncio.create_task(self._write_worker())
        self.logger.info("StateManager started")
    
    async def stop(self):
        """Stop the write worker gracefully"""
        self.logger.info("StateManager shutdown initiated")
        
        # Signal shutdown
        self._shutdown_event.set()
        
        # Wait for queue to drain
        start_time = asyncio.get_event_loop().time()
        while not self.write_queue.empty() or self._pending_writes > 0:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > self._shutdown_timeout:
                self.logger.warning(
                    f"Shutdown timeout reached with {self.write_queue.qsize()} items in queue "
                    f"and {self._pending_writes} pending writes"
                )
                break
            
            await asyncio.sleep(0.1)
            
        # Cancel writer task if still running
        if self._writer_task and not self._writer_task.done():
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
        
        self.logger.info("StateManager stopped")
    
    async def _write_worker(self):
        """Single writer to avoid contention"""
        self.logger.info("Write worker started")
        
        while not self._shutdown_event.is_set() or not self.write_queue.empty():
            try:
                # Use timeout to check shutdown event periodically
                operation, args = await asyncio.wait_for(
                    self.write_queue.get(), 
                    timeout=1.0
                )
                
                self._pending_writes += 1
                
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        if operation == "create_job":
                            self._create_job(conn, *args)
                        elif operation == "update_job_status":
                            self._update_job_status(conn, *args)
                        elif operation == "add_agent_result":
                            self._add_agent_result(conn, *args)
                        elif operation == "update_agent_status":
                            self._update_agent_status(conn, *args)
                        
                        conn.commit()
                        
                except Exception as e:
                    self.logger.error(f"Database write error: {e}")
                finally:
                    self._pending_writes -= 1
                    
            except asyncio.TimeoutError:
                # Check if we should shutdown
                continue
            except asyncio.CancelledError:
                # Graceful shutdown
                self.logger.info("Write worker cancelled, performing final flush")
                
                # Final flush of remaining items
                while not self.write_queue.empty():
                    try:
                        operation, args = self.write_queue.get_nowait()
                        with sqlite3.connect(self.db_path) as conn:
                            if operation == "create_job":
                                self._create_job(conn, *args)
                            elif operation == "update_job_status":
                                self._update_job_status(conn, *args)
                            elif operation == "add_agent_result":
                                self._add_agent_result(conn, *args)
                            elif operation == "update_agent_status":
                                self._update_agent_status(conn, *args)
                            conn.commit()
                    except Exception as e:
                        self.logger.error(f"Error during final flush: {e}")
                
                raise
            except Exception as e:
                self.logger.error(f"Write worker error: {e}")
                await asyncio.sleep(1)  # Brief pause before retry
        
        self.logger.info("Write worker stopped")
    
    def _create_job(self, conn, job_id: str, query: str):
        conn.execute(
            "INSERT INTO jobs (id, query, status) VALUES (?, ?, ?)",
            (job_id, query, "created")
        )
    
    def _update_job_status(self, conn, job_id: str, status: str):
        conn.execute(
            "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, job_id)
        )
    
    def _add_agent_result(self, conn, job_id: str, agent: str, result: Dict[str, Any]):
        conn.execute(
            "INSERT INTO agent_results (job_id, agent, result, metadata) VALUES (?, ?, ?, ?)",
            (job_id, agent, json.dumps(result.get("data", {})), json.dumps(result.get("metadata", {})))
        )
    
    def _update_agent_status(self, conn, agent: str, status: str, success: bool):
        if success:
            conn.execute("""
                INSERT OR REPLACE INTO agent_status (agent, status, last_success, consecutive_failures)
                VALUES (?, ?, CURRENT_TIMESTAMP, 0)
            """, (agent, status))
        else:
            conn.execute("""
                INSERT OR REPLACE INTO agent_status (agent, status, consecutive_failures, updated_at)
                VALUES (?, ?, 
                    COALESCE((SELECT consecutive_failures + 1 FROM agent_status WHERE agent = ?), 1),
                    CURRENT_TIMESTAMP)
            """, (agent, status, agent))
    
    # Async methods for agents to call
    async def create_job(self, job_id: str, query: str):
        await self.write_queue.put(("create_job", (job_id, query)))
    
    async def update_job_status(self, job_id: str, status: str):
        await self.write_queue.put(("update_job_status", (job_id, status)))
    
    async def add_agent_result(self, job_id: str, agent: str, result: Dict[str, Any]):
        await self.write_queue.put(("add_agent_result", (job_id, agent, result)))
    
    async def update_agent_status(self, agent: str, status: str, success: bool = True):
        await self.write_queue.put(("update_agent_status", (agent, status, success)))
    
    # Read methods (safe for concurrent access)
    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    async def get_agent_results(self, job_id: str, agent: Optional[str] = None) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if agent:
                cursor = conn.execute(
                    "SELECT * FROM agent_results WHERE job_id = ? AND agent = ?",
                    (job_id, agent)
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM agent_results WHERE job_id = ?",
                    (job_id,)
                )
            return [dict(row) for row in cursor.fetchall()]
    
    # Status methods
    async def get_queue_status(self) -> Dict[str, Any]:
        """Get current queue status"""
        return {
            "queue_size": self.write_queue.qsize(),
            "pending_writes": self._pending_writes,
            "is_shutting_down": self._shutdown_event.is_set(),
            "writer_running": self._writer_task and not self._writer_task.done()
        }
    
    async def wait_for_writes(self, timeout: float = 10.0) -> bool:
        """Wait for all pending writes to complete"""
        start_time = asyncio.get_event_loop().time()
        
        while self.write_queue.qsize() > 0 or self._pending_writes > 0:
            if asyncio.get_event_loop().time() - start_time > timeout:
                return False
            await asyncio.sleep(0.1)
        
        return True
    
    def is_healthy(self) -> bool:
        """Check if StateManager is healthy"""
        return (
            not self._shutdown_event.is_set() and
            self._writer_task is not None and
            not self._writer_task.done() and
            self.write_queue.qsize() < 1000  # Queue not overloaded
        )