import asyncio
import json
import logging
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("ToolProtocol")

class ToolCallStatus(Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class ToolCall:
    """Represents a tool call request from one agent to another"""
    id: str
    source_agent: str
    target_agent: str
    action: str
    parameters: Dict[str, Any]
    context: Dict[str, Any]
    status: ToolCallStatus = ToolCallStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class ToolRegistry:
    """Registry for agent tools and capabilities"""
    
    def __init__(self):
        self.tools: Dict[str, Dict[str, Callable]] = {}
        self.agent_capabilities: Dict[str, List[str]] = {}
        
    def register_tool(self, agent_name: str, tool_name: str, 
                     handler: Callable, description: str = ""):
        """Register a tool that an agent provides"""
        if agent_name not in self.tools:
            self.tools[agent_name] = {}
            self.agent_capabilities[agent_name] = []
            
        self.tools[agent_name][tool_name] = handler
        self.agent_capabilities[agent_name].append({
            "name": tool_name,
            "description": description
        })
        
        logger.info(f"Registered tool {tool_name} for agent {agent_name}")
    
    def get_tool(self, agent_name: str, tool_name: str) -> Optional[Callable]:
        """Get a tool handler"""
        return self.tools.get(agent_name, {}).get(tool_name)
    
    def get_agent_capabilities(self, agent_name: str) -> List[Dict[str, str]]:
        """Get list of capabilities for an agent"""
        return self.agent_capabilities.get(agent_name, [])
    
    def get_all_capabilities(self) -> Dict[str, List[Dict[str, str]]]:
        """Get all registered capabilities"""
        return self.agent_capabilities

class ToolCallingMixin:
    """Mixin for agents to add tool calling capabilities"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tool_registry: Optional[ToolRegistry] = None
        self.pending_tool_calls: Dict[str, ToolCall] = {}
        
    def set_tool_registry(self, registry: ToolRegistry):
        """Set the tool registry for this agent"""
        self.tool_registry = registry
        self._register_agent_tools()
    
    def _register_agent_tools(self):
        """Override this to register agent-specific tools"""
        pass
    
    async def call_tool(self, target_agent: str, action: str, 
                       parameters: Dict[str, Any], 
                       context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Call a tool from another agent"""
        if not self.tool_registry:
            raise RuntimeError("Tool registry not set")
        
        import uuid
        tool_call = ToolCall(
            id=str(uuid.uuid4()),
            source_agent=self.agent_name,
            target_agent=target_agent,
            action=action,
            parameters=parameters,
            context=context or {}
        )
        
        self.logger.info(f"Calling tool {target_agent}.{action} from {self.agent_name}")
        
        # Get the tool handler
        handler = self.tool_registry.get_tool(target_agent, action)
        if not handler:
            raise ValueError(f"Tool {target_agent}.{action} not found")
        
        try:
            # Execute the tool
            tool_call.status = ToolCallStatus.EXECUTING
            result = await handler(tool_call)
            
            tool_call.status = ToolCallStatus.COMPLETED
            tool_call.result = result
            
            self.logger.info(f"Tool call {tool_call.id} completed successfully")
            return result
            
        except Exception as e:
            tool_call.status = ToolCallStatus.FAILED
            tool_call.error = str(e)
            self.logger.error(f"Tool call {tool_call.id} failed: {e}")
            raise
    
    async def evaluate_and_request_help(self, 
                                      current_result: Dict[str, Any],
                                      quality_threshold: float = 0.6) -> Optional[Dict[str, Any]]:
        """Evaluate current results and request help if needed"""
        
        # Check if we have quality metrics
        confidence = current_result.get("confidence_score", 1.0)
        needs_more = current_result.get("needs_more_research", False)
        
        if confidence < quality_threshold or needs_more:
            self.logger.info(f"{self.agent_name} requesting additional help (confidence: {confidence})")
            
            # Determine what kind of help is needed
            help_requests = []
            
            if needs_more:
                # Request more research
                help_requests.append({
                    "agent": "preparer",
                    "action": "generate_followup_searches",
                    "reason": "insufficient_data"
                })
            
            if confidence < quality_threshold:
                # Request validation
                help_requests.append({
                    "agent": "validator",
                    "action": "deep_validate",
                    "reason": "low_confidence"
                })
            
            return {"help_needed": help_requests, "original_result": current_result}
        
        return None

class FeedbackLoop:
    """Manages feedback loops between agents"""
    
    def __init__(self, max_iterations: int = 3):
        self.max_iterations = max_iterations
        self.iteration_history: List[Dict[str, Any]] = []
        
    async def execute_with_feedback(self, 
                                   initial_task: Callable,
                                   quality_evaluator: Callable,
                                   improvement_task: Callable,
                                   context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a task with feedback loop for improvement"""
        
        iteration = 0
        current_result = None
        
        while iteration < self.max_iterations:
            if iteration == 0:
                # Initial execution
                current_result = await initial_task(context)
            else:
                # Improvement execution
                current_result = await improvement_task(context, current_result)
            
            # Evaluate quality
            evaluation = await quality_evaluator(current_result)
            
            self.iteration_history.append({
                "iteration": iteration,
                "result": current_result,
                "evaluation": evaluation
            })
            
            if evaluation.get("sufficient", False):
                logger.info(f"Quality threshold met after {iteration + 1} iterations")
                break
                
            context["previous_result"] = current_result
            context["evaluation"] = evaluation
            iteration += 1
        
        return {
            "final_result": current_result,
            "iterations": iteration + 1,
            "history": self.iteration_history
        }

class AgentToolkit:
    """Standard toolkit for common agent operations"""
    
    @staticmethod
    async def request_more_searches(tool_call: ToolCall) -> Dict[str, Any]:
        """Request additional search queries"""
        query = tool_call.parameters.get("query", "")
        previous_searches = tool_call.parameters.get("previous_searches", [])
        gaps = tool_call.parameters.get("identified_gaps", [])
        
        # Generate new search queries targeting gaps
        new_queries = []
        for gap in gaps:
            new_queries.append(f"{query} {gap}")
        
        return {
            "new_searches": new_queries,
            "reasoning": "Targeting identified information gaps"
        }
    
    @staticmethod
    async def request_deep_analysis(tool_call: ToolCall) -> Dict[str, Any]:
        """Request deeper analysis of content"""
        content = tool_call.parameters.get("content", "")
        focus_areas = tool_call.parameters.get("focus_areas", [])
        
        return {
            "analysis_type": "deep",
            "focus_areas": focus_areas,
            "recommendations": ["Check primary sources", "Verify claims", "Extract key metrics"]
        }
    
    @staticmethod
    async def consolidate_results(tool_call: ToolCall) -> Dict[str, Any]:
        """Consolidate results from multiple sources"""
        results = tool_call.parameters.get("results", [])
        
        # Simple consolidation logic
        consolidated = {
            "total_sources": len(results),
            "high_confidence_findings": [],
            "conflicting_information": [],
            "gaps_remaining": []
        }
        
        # Analyze results for patterns
        for result in results:
            if result.get("confidence_score", 0) > 0.8:
                consolidated["high_confidence_findings"].append(result.get("key_finding"))
        
        return consolidated