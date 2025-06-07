import logging
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from datetime import datetime
import asyncio

from tool_protocol import ToolCallingMixin
from error_handler import ErrorRecoveryHandler, ErrorClassifier, ErrorType
from session_pool import SessionPool

class BaseAgent(ToolCallingMixin):
    """Base class for all agents with error recovery and connection pooling"""
    
    def __init__(self, agent_name: str, config: Dict[str, Any], state_manager: Any):
        super().__init__()
        self.agent_name = agent_name
        self.config = config
        self.state_manager = state_manager
        self.session_pool: Optional[SessionPool] = None  # Set by orchestrator
        
        # Get LLM config for this agent
        self.llm_config = self._get_llm_config(agent_name)
        
        # Setup logging
        log_dir = Path(config["logging"]["dir"])
        log_dir.mkdir(exist_ok=True)
        
        self.logger = logging.getLogger(f"{agent_name}Agent")
        handler = logging.FileHandler(log_dir / f"{agent_name.lower()}.log")
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        self.logger.addHandler(handler)
        self.logger.setLevel(config["logging"]["level"])
        
        # Circuit breaker settings
        self.consecutive_failures = 0
        self.circuit_breaker_threshold = config.get("circuit_breaker", {}).get("threshold", 5)
        self.circuit_breaker_timeout = config.get("circuit_breaker", {}).get("timeout", 60)
        self.circuit_open = False
        self.circuit_opened_at = None
        
        # Error recovery
        self.error_handler = ErrorRecoveryHandler(config)
    
    def _get_llm_config(self, agent: str) -> Dict[str, Any]:
        """Get LLM configuration for agent"""
        cfg = self.config["llm_endpoints"].get(agent, {})
        return {
            "host": cfg.get("host", "http://localhost:11434"),
            "model": cfg.get("model", "llama3"),
            "api_key": cfg.get("api_key"),
            "temperature": cfg.get("temperature", 0.7),
            "max_tokens": cfg.get("max_tokens", 2048)
        }
    
    async def update_status(self, status: str, success: bool = True):
        """Update agent status in state manager"""
        if success:
            self.consecutive_failures = 0
            if self.circuit_open:
                self.logger.info(f"Circuit breaker closed for {self.agent_name}")
                self.circuit_open = False
                self.circuit_opened_at = None
        else:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.circuit_breaker_threshold and not self.circuit_open:
                self.circuit_open = True
                self.circuit_opened_at = datetime.utcnow()
                self.logger.error(f"Circuit breaker opened for {self.agent_name}")
        
        await self.state_manager.update_agent_status(self.agent_name, status, success)
    
    async def add_result(self, job_id: str, result: Dict[str, Any]):
        """Add agent result to state manager"""
        await self.state_manager.add_agent_result(job_id, self.agent_name, {"data": result})
    
    def is_circuit_open(self) -> bool:
        """Check if circuit breaker is open and should remain open"""
        if not self.circuit_open:
            return False
        
        # Check if timeout has expired
        if self.circuit_opened_at:
            elapsed = (datetime.utcnow() - self.circuit_opened_at).total_seconds()
            if elapsed > self.circuit_breaker_timeout:
                self.logger.info(f"Circuit breaker timeout expired for {self.agent_name}, attempting reset")
                return False  # Allow one attempt to test
        
        return True
    
    async def execute_with_recovery(self, 
                                   func: Callable,
                                   context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute agent function with error recovery"""
        
        # Check circuit breaker
        if self.is_circuit_open():
            self.logger.warning(f"Circuit breaker open for {self.agent_name}, using fallback")
            return await self.error_handler.get_fallback_result(
                self.agent_name, 
                context, 
                Exception("Circuit breaker open"),
                []
            )
        
        # Execute with retry logic
        try:
            result = await self.error_handler.handle_with_retry(
                func,
                self.agent_name,
                context
            )
            
            # Update status on success
            await self.update_status("healthy", True)
            
            return result
            
        except Exception as e:
            # Update status on failure
            await self.update_status("error", False)
            raise