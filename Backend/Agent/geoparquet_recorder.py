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
    satisfaction_source: str = "none"              # NEW: visual, amenity, combined, none
    satisfaction_reasoning: Optional[str] = None   # NEW: LLM reasoning for satisfaction
    
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
            'satisfaction_source': self.satisfaction_source,
            'satisfaction_reasoning': self.satisfaction_reasoning,
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
        max_buffer_size: int = 500,  # Reduced for crash safety
        include_thoughts: bool = True,
        include_perception: bool = True,
        perception_mode: str = "both",
        auto_flush_interval: float = 2.0,  # Auto-flush every 2 seconds
        keep_temp_files: bool = True,
    ):
        """
        Initialize the recorder.

        Args:
            output_dir: Directory to save GeoParquet files (default: Documentation/)
            max_buffer_size: Max records to buffer before flushing to disk
            include_thoughts: Whether to include thought stream data
            include_perception: Whether to include street perception data
            perception_mode: Agent perception mode ('amenities', 'perception', or 'both')
            auto_flush_interval: Seconds between auto-flush to temp file
            keep_temp_files: Keep .tmp files for crash recovery
        """
        if output_dir is None:
            output_dir = Path(__file__).parent.parent.parent / "Documentation"

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

        self.max_buffer_size = max_buffer_size
        self.include_thoughts = include_thoughts
        self.include_perception = include_perception
        self.perception_mode = perception_mode
        self.auto_flush_interval = auto_flush_interval
        self.keep_temp_files = keep_temp_files

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

        # Auto-flush timer
        self._flush_timer: Optional[threading.Timer] = None
        self._flush_timer_lock = threading.Lock()
        self._last_flush_time: float = 0
        self._temp_file_counter: int = 0
        self._temp_files: List[Path] = []

        logger.info(
            f"GeoParquetRecorder initialized. Output: {self.output_dir} | "
            f"Auto-flush: {auto_flush_interval}s | Buffer: {max_buffer_size}"
        )
    
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
        satisfaction_source = "none"
        satisfaction_reasoning = None

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
                satisfaction_source = data.get('satisfaction_source', 'none')
                satisfaction_reasoning = data.get('satisfaction_reasoning', None)
        
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
                # Use synchronous access to _store (StreamMemory internal storage)
                stream_memory = agent.memory.stream
                if hasattr(stream_memory, '_store'):
                    for topic, events in stream_memory._store.items():
                        # events is a deque of MemoryNode objects
                        events_list = list(events)[-10:]  # Last 10 events per topic
                        for event in events_list:
                            thought_stream.append({
                                'topic': topic,
                                'step': event.step,
                                'description': event.description,
                            })
            except Exception as e:
                logger.warning(f"Could not extract thought stream: {e}")
        
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
            satisfaction_source=satisfaction_source,
            satisfaction_reasoning=satisfaction_reasoning,
        )
    
    def _flush_to_parquet(self, is_final_flush: bool = False) -> Optional[Path]:
        """
        Flush buffered records to GeoParquet file.
        
        Args:
            is_final_flush: If True, write directly to final file. If False, write to temp file.
        
        Returns:
            Path to the written file, or None if failed
        """
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
            mode_suffix = f"_{self.perception_mode}" if self.perception_mode else ""
            
            if is_final_flush and len(self._temp_files) == 0:
                # Only one flush total, write directly to final file
                filename = f"agent_recording_{self.session_name or timestamp}{mode_suffix}.parquet"
                file_path = self.output_dir / filename
            else:
                # Write to temp file (will be merged later)
                self._temp_file_counter += 1
                filename = f"agent_recording_{self.session_name or timestamp}_flush_{self._temp_file_counter:03d}.tmp.parquet"
                file_path = self.output_dir / filename
                self._temp_files.append(file_path)

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

    def _merge_temp_files(self) -> Optional[Path]:
        """
        Merge all temp flush files into a final GeoParquet file.
        
        Returns:
            Path to the merged file, or None if failed
        """
        if not self._temp_files:
            logger.debug("No temp files to merge")
            return None
        
        try:
            import geopandas as gpd
            import pandas as pd
            
            # Read all temp files
            gdfs = []
            for temp_file in self._temp_files:
                if temp_file.exists():
                    gdf = gpd.read_parquet(str(temp_file))
                    gdfs.append(gdf)
                    logger.debug(f"Loaded temp file: {temp_file.name} ({len(gdf)} records)")
            
            if not gdfs:
                logger.error("No valid temp files found to merge")
                return None
            
            # Concatenate all GeoDataFrames
            merged_gdf = pd.concat(gdfs, ignore_index=True)
            merged_gdf = gpd.GeoDataFrame(merged_gdf, crs="EPSG:4326")
            
            # Generate final filename
            mode_suffix = f"_{self.perception_mode}" if self.perception_mode else ""
            filename = f"agent_recording_{self.session_name}{mode_suffix}.parquet"
            final_path = self.output_dir / filename
            
            # Export merged GeoDataFrame
            merged_gdf.to_parquet(str(final_path))
            
            total_records = sum(len(gdf) for gdf in gdfs)
            logger.info(f"Merged {len(self._temp_files)} temp files ({total_records} records) into {final_path.name}")
            
            # Clean up temp files if not keeping them
            if not self.keep_temp_files:
                for temp_file in self._temp_files:
                    try:
                        temp_file.unlink()
                        logger.debug(f"Deleted temp file: {temp_file}")
                    except Exception as e:
                        logger.warning(f"Failed to delete temp file {temp_file}: {e}")
            
            return final_path
            
        except Exception as e:
            logger.error(f"Failed to merge temp files: {e}")
            return None

    def stop_recording(self) -> Optional[Path]:
        """
        Stop recording and export to GeoParquet.
        
        Merges all temp flush files into a final parquet file.

        Returns:
            Path to the exported GeoParquet file, or None if no data recorded
        """
        if not self.is_recording:
            logger.warning("No recording in progress")
            return None

        self.is_recording = False

        # Flush remaining buffer (this will be added to temp files)
        self._flush_to_parquet()

        # Merge all temp files into final parquet
        if self._temp_files:
            final_path = self._merge_temp_files()
        else:
            # No temp files, use the single flush file (rename if needed)
            logger.warning("No temp files found - recording may have failed")
            final_path = None

        logger.info(f"Recording stopped. Total records: {self.total_records}")

        return final_path

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
            'temp_files_count': len(self._temp_files),
        }

    def get_output_path(self) -> Optional[Path]:
        """Get the expected output file path."""
        if not self.session_name:
            return None
        mode_suffix = f"_{self.perception_mode}" if self.perception_mode else ""
        return self.output_dir / f"agent_recording_{self.session_name}{mode_suffix}.parquet"


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
    perception_mode: str = "both",
) -> GeoParquetRecorder:
    """Create and set the global recorder instance."""
    global _recorder
    with _recorder_lock:
        _recorder = GeoParquetRecorder(
            output_dir=output_dir,
            max_buffer_size=max_buffer_size,
            include_thoughts=include_thoughts,
            include_perception=include_perception,
            perception_mode=perception_mode,
        )
        return _recorder


def clear_recorder() -> None:
    """Clear the global recorder instance."""
    global _recorder
    with _recorder_lock:
        if _recorder and _recorder.is_recording:
            _recorder.stop_recording()
        _recorder = None


def recover_unmerged_sessions(
    output_dir: Optional[Path] = None,
    keep_temp_files: bool = True,
) -> List[Path]:
    """
    Recover unmerged temp files from crashed recording sessions.
    
    Scans the output directory for temp flush files (*.tmp.parquet),
    groups them by session name, merges each group, and saves the final parquet.
    
    Args:
        output_dir: Directory to scan (default: Documentation/)
        keep_temp_files: Whether to keep temp files after merging
    
    Returns:
        List of recovered file paths
    """
    if output_dir is None:
        output_dir = Path(__file__).parent.parent.parent / "Documentation"
    
    output_dir = Path(output_dir)
    if not output_dir.exists():
        logger.debug(f"Output directory does not exist: {output_dir}")
        return []
    
    # Find all temp flush files
    temp_files = list(output_dir.glob("agent_recording_*_flush_*.tmp.parquet"))
    
    if not temp_files:
        logger.debug("No unmerged temp files found")
        return []
    
    logger.info(f"Found {len(temp_files)} unmerged temp files for recovery")
    
    # Group by session name
    # Pattern: agent_recording_{session_name}_flush_{counter:03d}.tmp.parquet
    from collections import defaultdict
    session_groups: Dict[str, List[Path]] = defaultdict(list)
    
    for temp_file in temp_files:
        # Extract session name from filename
        # agent_recording_SESSIONNAME_flush_001.tmp.parquet
        filename = temp_file.name
        # Remove prefix and suffix
        if filename.startswith("agent_recording_"):
            remainder = filename[len("agent_recording_"):-len(".tmp.parquet")]
            # Find last _flush_ to extract session name
            flush_idx = remainder.rfind("_flush_")
            if flush_idx > 0:
                session_name = remainder[:flush_idx]
                session_groups[session_name].append(temp_file)
    
    # Merge each session group
    recovered_paths = []
    
    for session_name, files in session_groups.items():
        logger.info(f"Recovering session '{session_name}' with {len(files)} temp files")
        
        try:
            import geopandas as gpd
            import pandas as pd
            
            # Sort files by flush number
            files.sort(key=lambda p: p.name)
            
            # Read all temp files
            gdfs = []
            for temp_file in files:
                gdf = gpd.read_parquet(str(temp_file))
                gdfs.append(gdf)
            
            if not gdfs:
                logger.error(f"No valid temp files for session '{session_name}'")
                continue
            
            # Merge
            merged_gdf = pd.concat(gdfs, ignore_index=True)
            merged_gdf = gpd.GeoDataFrame(merged_gdf, crs="EPSG:4326")
            
            # Determine perception mode from first temp file
            mode_suffix = ""
            first_name = files[0].name
            if "_both" in first_name:
                mode_suffix = "_both"
            elif "_amenities" in first_name:
                mode_suffix = "_amenities"
            elif "_perception" in first_name:
                mode_suffix = "_perception"
            
            # Save final file
            filename = f"agent_recording_{session_name}{mode_suffix}.parquet"
            final_path = output_dir / filename
            merged_gdf.to_parquet(str(final_path))
            
            total_records = sum(len(gdf) for gdf in gdfs)
            logger.info(f"Recovered session '{session_name}': {total_records} records -> {final_path.name}")
            
            recovered_paths.append(final_path)
            
            # Clean up temp files if not keeping them
            if not keep_temp_files:
                for temp_file in files:
                    try:
                        temp_file.unlink()
                        logger.debug(f"Deleted temp file: {temp_file}")
                    except Exception as e:
                        logger.warning(f"Failed to delete temp file {temp_file}: {e}")
            
        except Exception as e:
            logger.error(f"Failed to recover session '{session_name}': {e}")
    
    return recovered_paths
