"""
GeoParquet Recorder - Records agent behaviors to GeoParquet format for spatial ML analytics.

This module captures all agent behaviors including:
- Spatial coordinates (longitude, latitude)
- Edge IDs and network position
- Agent decisions and LLM reasoning
- Needs state (hunger, energy, social)
- Cognition state (mood, curiosity, fatigue)
- Visited amenities and perception points
- Thought streams (mobility, cognition, needs events)

Output: GeoParquet file compatible with QGIS, ArcGIS, pandas, DuckDB, and BigQuery.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
import threading

logger = logging.getLogger(__name__)


@dataclass
class AgentRecord:
    """Single record of an agent's state at a given step."""
    agent_id: int
    step: int
    timestamp: str
    longitude: float
    latitude: float
    edge_id: Optional[int]
    position_along_edge: float
    archetype: str
    age: int
    needs: Dict[str, float] = field(default_factory=dict)
    cognition_state: Dict[str, Any] = field(default_factory=dict)
    current_plan: Dict[str, Any] = field(default_factory=dict)
    visited_edges: Dict[str, int] = field(default_factory=dict)
    visited_amenities: List[Dict] = field(default_factory=list)
    nearby_amenities: List[Dict] = field(default_factory=list)
    street_perception: Optional[Dict] = None
    thought_stream: List[Dict] = field(default_factory=list)
    decision_reason: Optional[str] = None
    is_fallback: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for DataFrame conversion."""
        return {
            'agent_id': self.agent_id,
            'step': self.step,
            'timestamp': self.timestamp,
            'longitude': self.longitude,
            'latitude': self.latitude,
            'edge_id': self.edge_id if self.edge_id is not None else -1,
            'position_along_edge': self.position_along_edge,
            'archetype': self.archetype,
            'age': self.age,
            'needs_json': json.dumps(self.needs),
            'cognition_state_json': json.dumps(self.cognition_state),
            'current_plan_json': json.dumps(self.current_plan),
            'visited_edges_json': json.dumps(self.visited_edges),
            'visited_amenities_json': json.dumps(self.visited_amenities),
            'nearby_amenities_json': json.dumps(self.nearby_amenities),
            'street_perception_json': json.dumps(self.street_perception) if self.street_perception else None,
            'thought_stream_json': json.dumps(self.thought_stream),
            'decision_reason': self.decision_reason,
            'is_fallback': self.is_fallback,
        }


class GeoParquetRecorder:
    """
    Records agent behaviors during simulation and exports to GeoParquet.
    
    Usage:
        recorder = GeoParquetRecorder(output_dir=Path("Documentation"))
        recorder.start_recording(session_name="experiment_001")
        
        # During simulation, call for each agent:
        recorder.record_agent_state(agent, step, decision_data)
        
        # After simulation:
        recorder.stop_recording()  # Exports to GeoParquet
    """
    
    def __init__(
        self,
        output_dir: Optional[Path] = None,
        max_buffer_size: int = 10000,
        include_thoughts: bool = True,
        include_perception: bool = True,
    ):
        """
        Initialize the recorder.
        
        Args:
            output_dir: Directory to save GeoParquet files (default: Documentation/)
            max_buffer_size: Max records to buffer before flushing to disk
            include_thoughts: Whether to include thought stream data
            include_perception: Whether to include street perception data
        """
        if output_dir is None:
            output_dir = Path(__file__).parent.parent.parent / "Documentation"
        
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        self.max_buffer_size = max_buffer_size
        self.include_thoughts = include_thoughts
        self.include_perception = include_perception
        
        # Recording state
        self.is_recording = False
        self.session_name: Optional[str] = None
        self.session_id: Optional[str] = None
        self.start_time: Optional[datetime] = None
        self.start_step: int = 0
        
        # Data buffer
        self.buffer: List[AgentRecord] = []
        self.buffer_lock = threading.Lock()
        self.total_records = 0
        
        # Statistics
        self.stats = {
            'agents_tracked': set(),
            'steps_recorded': 0,
            'records_written': 0,
        }
        
        logger.info(f"GeoParquetRecorder initialized. Output: {self.output_dir}")
    
    def start_recording(self, session_name: Optional[str] = None) -> str:
        """
        Start a new recording session.
        
        Args:
            session_name: Optional name for the session (used in filename)
            
        Returns:
            Session ID string
        """
        if self.is_recording:
            logger.warning("Recording already in progress")
            return self.session_id
        
        self.session_name = session_name or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_id = f"{self.session_name}_{id(self)}"
        self.start_time = datetime.now()
        self.start_step = 0
        self.is_recording = True
        self.buffer = []
        self.total_records = 0
        self.stats = {
            'agents_tracked': set(),
            'steps_recorded': 0,
            'records_written': 0,
        }
        
        logger.info(f"Recording started: {self.session_id}")
        return self.session_id
    
    def stop_recording(self) -> Optional[Path]:
        """
        Stop recording and export to GeoParquet.
        
        Returns:
            Path to the exported GeoParquet file, or None if no data recorded
        """
        if not self.is_recording:
            logger.warning("No recording in progress")
            return None
        
        self.is_recording = False
        
        # Flush remaining buffer
        file_path = self._flush_to_parquet()
        
        logger.info(f"Recording stopped. Total records: {self.total_records}")
        
        return file_path
    
    def record_agent_state(
        self,
        agent: Any,
        step: int,
        decision_reason: Optional[str] = None,
        is_fallback: bool = False,
    ) -> None:
        """
        Record an agent's state at a given step.
        
        Args:
            agent: CityAgent instance to record
            step: Current simulation step
            decision_reason: LLM reasoning for the last decision (optional)
            is_fallback: Whether the decision used rule-based fallback
        """
        if not self.is_recording:
            return
        
        try:
            # Extract agent data
            record = self._create_agent_record(
                agent=agent,
                step=step,
                decision_reason=decision_reason,
                is_fallback=is_fallback,
            )
            
            with self.buffer_lock:
                self.buffer.append(record)
                self.total_records += 1
                self.stats['agents_tracked'].add(agent.unique_id)
                self.stats['steps_recorded'] = max(self.stats['steps_recorded'], step)
                
                # Auto-flush if buffer is full
                if len(self.buffer) >= self.max_buffer_size:
                    self._flush_to_parquet()
                    
        except Exception as e:
            logger.error(f"Failed to record agent {agent.unique_id}: {e}")
    
    def _create_agent_record(
        self,
        agent: Any,
        step: int,
        decision_reason: Optional[str] = None,
        is_fallback: bool = False,
    ) -> AgentRecord:
        """Create an AgentRecord from an agent instance."""
        # Get geometry coordinates
        geom = agent.geometry
        longitude = geom.x if geom else 0.0
        latitude = geom.y if geom else 0.0
        
        # Get memory data (with defaults if not available)
        needs = {}
        cognition_state = {}
        current_plan = {}
        visited_edges = {}
        visited_amenities = []
        agent_profile = {}
        
        if hasattr(agent, 'memory'):
            # Use synchronous access to avoid asyncio issues
            # Access internal data directly for performance
            if hasattr(agent.memory, 'status') and hasattr(agent.memory.status, '_data'):
                data = agent.memory.status._data
                needs = data.get('needs', {})
                cognition_state = data.get('cognition_state', {})
                current_plan = data.get('current_plan', {})
                visited_edges = data.get('visited_edges', {})
                visited_amenities = data.get('visited_amenities', [])
                agent_profile = data.get('agent_profile', {})
        
        # Get nearby amenities from agent
        nearby_amenities = getattr(agent, 'nearby_amenities', [])
        
        # Get street perception (if enabled)
        street_perception = None
        if self.include_perception:
            street_perception = getattr(agent, 'street_perception', None)
        
        # Get thought stream (if enabled)
        thought_stream = []
        if self.include_thoughts and hasattr(agent, 'memory') and hasattr(agent.memory, 'stream'):
            try:
                # Get recent events from stream memory
                stream_memory = agent.memory.stream
                if hasattr(stream_memory, '_data'):
                    for topic, events in stream_memory._data.items():
                        for event in events[-10:]:  # Last 10 events per topic
                            thought_stream.append({
                                'topic': topic,
                                'step': event.step if hasattr(event, 'step') else step,
                                'description': event.description if hasattr(event, 'description') else str(event),
                            })
            except Exception as e:
                logger.debug(f"Could not extract thought stream: {e}")
        
        return AgentRecord(
            agent_id=agent.unique_id,
            step=step,
            timestamp=datetime.now().isoformat(),
            longitude=longitude,
            latitude=latitude,
            edge_id=getattr(agent, 'current_edge_id', None),
            position_along_edge=getattr(agent, 'position_along_edge', 0.0),
            archetype=agent_profile.get('archetype', 'unknown'),
            age=agent_profile.get('age', 0),
            needs=needs,
            cognition_state=cognition_state,
            current_plan=current_plan,
            visited_edges=visited_edges,
            visited_amenities=visited_amenities[-20:] if visited_amenities else [],  # Last 20
            nearby_amenities=nearby_amenities[:10] if nearby_amenities else [],  # Nearest 10
            street_perception=street_perception,
            thought_stream=thought_stream,
            decision_reason=decision_reason,
            is_fallback=is_fallback,
        )
    
    def _flush_to_parquet(self) -> Optional[Path]:
        """Flush buffered records to GeoParquet file."""
        if not self.buffer:
            logger.debug("No data to flush")
            return None
        
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            import pandas as pd
            
            # Convert buffer to list of dicts
            records = [record.to_dict() for record in self.buffer]
            
            # Create DataFrame
            df = pd.DataFrame(records)
            
            # Create geometry column from coordinates
            geometry = [Point(xy) for xy in zip(df['longitude'], df['latitude'])]
            
            # Create GeoDataFrame
            gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
            
            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"agent_recording_{self.session_name or timestamp}.parquet"
            file_path = self.output_dir / filename
            
            # Export to GeoParquet
            gdf.to_parquet(str(file_path))
            
            self.stats['records_written'] += len(self.buffer)
            self.buffer = []
            
            logger.info(f"Flushed {len(records)} records to {file_path}")
            return file_path
            
        except ImportError as e:
            logger.error(f"Missing dependencies for GeoParquet export: {e}")
            logger.error("Please install: pip install geopandas pyarrow")
            return None
        except Exception as e:
            logger.error(f"Failed to flush to GeoParquet: {e}")
            return None
    
    def get_status(self) -> Dict[str, Any]:
        """Get current recording status."""
        return {
            'is_recording': self.is_recording,
            'session_id': self.session_id,
            'session_name': self.session_name,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'start_step': self.start_step,
            'total_records': self.total_records,
            'buffer_size': len(self.buffer),
            'agents_tracked': len(self.stats['agents_tracked']),
            'steps_recorded': self.stats['steps_recorded'],
            'records_written': self.stats['records_written'],
        }
    
    def get_output_path(self) -> Optional[Path]:
        """Get the expected output file path."""
        if not self.session_name:
            return None
        return self.output_dir / f"agent_recording_{self.session_name}.parquet"


# Global recorder instance for the API server
_recorder: Optional[GeoParquetRecorder] = None
_recorder_lock = threading.Lock()


def get_recorder() -> Optional[GeoParquetRecorder]:
    """Get the global recorder instance."""
    with _recorder_lock:
        return _recorder


def create_recorder(
    output_dir: Optional[Path] = None,
    max_buffer_size: int = 10000,
    include_thoughts: bool = True,
    include_perception: bool = True,
) -> GeoParquetRecorder:
    """Create and set the global recorder instance."""
    global _recorder
    with _recorder_lock:
        _recorder = GeoParquetRecorder(
            output_dir=output_dir,
            max_buffer_size=max_buffer_size,
            include_thoughts=include_thoughts,
            include_perception=include_perception,
        )
        return _recorder


def clear_recorder() -> None:
    """Clear the global recorder instance."""
    global _recorder
    with _recorder_lock:
        if _recorder and _recorder.is_recording:
            _recorder.stop_recording()
        _recorder = None
