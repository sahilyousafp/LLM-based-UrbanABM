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
    satisfaction_source: str = "none"
    satisfaction_reasoning: Optional[str] = None
    start_lon: Optional[float] = None
    start_lat: Optional[float] = None
    target_name: Optional[str] = None
    target_amenity_type: Optional[str] = None
    target_lon: Optional[float] = None
    target_lat: Optional[float] = None

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
            'start_lon': self.start_lon,
            'start_lat': self.start_lat,
            'target_name': self.target_name,
            'target_amenity_type': self.target_amenity_type,
            'target_lon': self.target_lon,
            'target_lat': self.target_lat,
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
        max_buffer_size: int = 50,
        include_thoughts: bool = True,
        include_perception: bool = True,
        perception_mode: str = "both",
        auto_flush_interval: float = 2.0,
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
        self._recording_date: Optional[str] = None  # YYYY-MM-DD folder
        self._first_archetype: Optional[str] = None  # Track first archetype for output path

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

        # Temp file tracking for merge
        self._temp_file_counter: int = 0
        self._temp_files: List[Path] = []

        logger.info(
            f"GeoParquetRecorder initialized. Output: {self.output_dir} | "
            f"Buffer: {max_buffer_size}"
        )
    
    def start_recording(self, session_name: Optional[str] = None) -> str:
        if self.is_recording:
            logger.warning("Recording already in progress")
            return self.session_id
        
        self.session_name = session_name or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_id = f"{self.session_name}_{id(self)}"
        self.start_time = datetime.now()
        self.start_step = 0
        self._recording_date = self.start_time.strftime("%Y-%m-%d")
        self._first_archetype = None
        self.is_recording = True
        self.buffer = []
        self.total_records = 0
        self._temp_files = []
        self.stats = {
            'agents_tracked': set(),
            'steps_recorded': 0,
            'records_written': 0,
        }
        
        logger.info(f"Recording started: {self.session_id}")
        return self.session_id
        
        # Sequential run numbering
        existing = sorted(self.output_dir.glob("run_*.parquet"))
        run_num = len(existing) + 1
        self.session_name = session_name or f"run_{run_num:04d}"
        self.session_id = f"{self.session_name}_{id(self)}"
        self.start_time = datetime.now()
        self.start_step = 0
        self._recording_date = self.start_time.strftime("%Y-%m-%d")
        self._first_archetype = None
        self.is_recording = True
        self.buffer = []
        self.total_records = 0
        self._temp_files = []
        self.stats = {
            'agents_tracked': set(),
            'steps_recorded': 0,
            'records_written': 0,
        }
        
        logger.info(f"Recording started: {self.session_id}")
        return self.session_id

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

            # Capture first archetype for get_output_path utility
            if self._first_archetype is None and record.archetype:
                self._first_archetype = record.archetype

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

        destination = {}
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
                destination = data.get('destination', {})
        
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
            visited_amenities=visited_amenities[-20:] if visited_amenities else [],
            nearby_amenities=nearby_amenities[:10] if nearby_amenities else [],
            street_perception=street_perception,
            thought_stream=thought_stream,
            decision_reason=decision_reason,
            is_fallback=is_fallback,
            satisfaction_source=satisfaction_source,
            satisfaction_reasoning=satisfaction_reasoning,
            start_lon=destination.get('start_lon'),
            start_lat=destination.get('start_lat'),
            target_name=destination.get('name'),
            target_amenity_type=destination.get('amenity_type'),
            target_lon=destination.get('lon'),
            target_lat=destination.get('lat'),
        )
    
    def _flush_to_parquet(self, is_final_flush: bool = False) -> Optional[Path]:
        if not self.buffer:
            logger.debug("No data to flush")
            return None

        try:
            import geopandas as gpd
            from shapely.geometry import Point
            import pandas as pd

            records = [record.to_dict() for record in self.buffer]
            df = pd.DataFrame(records)
            geometry = [Point(lon, lat) for lon, lat in zip(df['longitude'], df['latitude'])]
            gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

            # Nested folder structure: date/archetype/perception_mode/
            archetypes = df['archetype'].unique()
            written_paths = []

            for archetype in archetypes:
                archetype_gdf = gdf[gdf['archetype'] == archetype]
                archetype_clean = archetype.lower().replace(' ', '_')
                base_dir = self.output_dir / self._recording_date / archetype_clean / self.perception_mode
                base_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{self.session_name}.parquet"
                file_path = base_dir / filename

                if file_path.exists():
                    existing = gpd.read_parquet(str(file_path))
                    archetype_gdf = pd.concat([existing, archetype_gdf], ignore_index=True)
                    archetype_gdf = gpd.GeoDataFrame(archetype_gdf, crs="EPSG:4326")

                archetype_gdf.to_parquet(str(file_path))
                written_paths.append(file_path)
                logger.info(f"Flushed {len(archetype_gdf)} records for '{archetype}' to {file_path}")

            self.stats['records_written'] += len(self.buffer)
            self.buffer = []
            return written_paths[0] if written_paths else None

        except ImportError as e:
            logger.error(f"Missing dependencies for GeoParquet export: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to flush to GeoParquet: {e}")
            return None

    def _merge_temp_files(self) -> Optional[Path]:
        """
        Merge all temp flush files into final GeoParquet files (one per archetype).
        
        Returns:
            Path to the first merged file, or None if failed
        """
        if not self._temp_files:
            logger.debug("No temp files to merge")
            return None
        
        try:
            import geopandas as gpd
            import pandas as pd
            
            # Read all temp files
            all_records = []
            for temp_file in self._temp_files:
                if temp_file.exists():
                    gdf = gpd.read_parquet(str(temp_file))
                    all_records.append(gdf)
                    logger.debug(f"Loaded temp file: {temp_file.name} ({len(gdf)} records)")
            
            if not all_records:
                logger.error("No valid temp files found to merge")
                return None
            
            # Concatenate all GeoDataFrames
            merged_gdf = pd.concat(all_records, ignore_index=True)
            merged_gdf = gpd.GeoDataFrame(merged_gdf, crs="EPSG:4326")
            
            # Group by archetype and save one file per archetype in the same folder structure
            archetypes = merged_gdf['archetype'].unique()
            written_paths = []
            
            for archetype in archetypes:
                archetype_gdf = merged_gdf[merged_gdf['archetype'] == archetype]
                archetype_clean = archetype.lower().replace(' ', '_')
                
                # Build folder structure: <date>/<archetype>/<perception_mode>/
                base_dir = self.output_dir / self._recording_date / archetype_clean / self.perception_mode
                base_dir.mkdir(parents=True, exist_ok=True)
                
                filename = f"agent_recording_{self.session_name}.parquet"
                final_path = base_dir / filename
                
                archetype_gdf.to_parquet(str(final_path))
                written_paths.append(final_path)
                logger.info(f"Merged {len(archetype_gdf)} records for archetype '{archetype}' -> {final_path.name}")
            
            # Clean up temp files if not keeping them
            if not self.keep_temp_files:
                for temp_file in self._temp_files:
                    try:
                        temp_file.unlink()
                        logger.debug(f"Deleted temp file: {temp_file}")
                    except Exception as e:
                        logger.warning(f"Failed to delete temp file {temp_file}: {e}")
            
            return written_paths[0] if written_paths else None
            
        except Exception as e:
            logger.error(f"Failed to merge temp files: {e}")
            return None

    def stop_recording(self) -> Optional[Path]:
        if not self.is_recording:
            logger.warning("No recording in progress")
            return None

        self.is_recording = False
        final_path = self._flush_to_parquet(is_final_flush=True)
        logger.info(f"Recording stopped. Total records: {self.total_records} -> {final_path}")
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
        if not self.session_name or not self._recording_date:
            return None
        archetype_folder = (self._first_archetype or "unknown").lower().replace(" ", "_")
        return self.output_dir / self._recording_date / archetype_folder / self.perception_mode / f"{self.session_name}.parquet"


# Global recorder instance for the API server
_recorder: Optional[GeoParquetRecorder] = None
_recorder_lock = threading.Lock()


def get_recorder() -> Optional[GeoParquetRecorder]:
    """Get the global recorder instance."""
    with _recorder_lock:
        return _recorder


def create_recorder(
    output_dir: Optional[Path] = None,
    max_buffer_size: int = 50,
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
    
    Scans the output directory recursively for temp flush files (*.tmp.parquet),
    groups them by session name within each archetype/perception_mode folder,
    merges each group, and saves the final parquet.
    
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
    
    # Find all temp flush files recursively
    temp_files = list(output_dir.rglob("agent_recording_*_flush_*.tmp.parquet"))
    
    if not temp_files:
        logger.debug("No unmerged temp files found")
        return []
    
    logger.info(f"Found {len(temp_files)} unmerged temp files for recovery")
    
    # Group by folder (date/archetype/perception_mode) and session name
    from collections import defaultdict
    session_groups: Dict[str, List[Path]] = defaultdict(list)
    
    for temp_file in temp_files:
        filename = temp_file.name
        if filename.startswith("agent_recording_"):
            remainder = filename[len("agent_recording_"):-len(".tmp.parquet")]
            flush_idx = remainder.rfind("_flush_")
            if flush_idx > 0:
                session_name = remainder[:flush_idx]
                # Group by parent folder to keep archetypes separate
                folder_key = str(temp_file.parent)
                session_groups[f"{folder_key}::{session_name}"].append(temp_file)
    
    # Merge each session group
    recovered_paths = []
    
    for group_key, files in session_groups.items():
        folder_path, session_name = group_key.rsplit("::", 1)
        logger.info(f"Recovering session '{session_name}' in {folder_path} with {len(files)} temp files")
        
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
            
            # Save final file in the same folder
            filename = f"agent_recording_{session_name}.parquet"
            final_path = Path(folder_path) / filename
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
