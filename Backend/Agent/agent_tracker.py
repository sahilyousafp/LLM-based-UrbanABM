"""
Agent Movement and Decision Tracker

This module stores agent movements and decisions in a DuckDB database with spatial indexing.
The data structure is designed for easy analysis, heatmap generation, and spatial visualization.
"""

import duckdb
from pathlib import Path
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AgentTracker:
    """
    Tracks agent movements and decisions in a DuckDB database with spatial indexing.
    
    Schema:
    - agent_movements: Records every position update with timestamp and spatial index
    - agent_decisions: Records decision points (edge changes) with context
    """
    
    def __init__(self, db_path=None):
        """
        Initialize the agent tracker.
        
        Args:
            db_path: Path to the DuckDB database file. If None, creates default path.
        """
        if db_path is None:
            # Default path in tracking_data folder
            tracker_dir = Path(__file__).parent / "tracking_data"
            tracker_dir.mkdir(exist_ok=True)
            db_path = tracker_dir / "agent_tracking.duckdb"
        
        self.db_path = Path(db_path)
        self.con = None
        self._initialize_database()
    
    def _initialize_database(self):
        """Initialize database connection and create tables with spatial indexing."""
        try:
            # Connect to database (creates if doesn't exist).
            # read_only=False is the default but we make it explicit; if another
            # uvicorn worker process already holds the write lock, the connect()
            # call will raise an IOException which is caught below.
            self.con = duckdb.connect(str(self.db_path), read_only=False)
            
            # Install and load spatial extension
            self.con.execute("INSTALL spatial;")
            self.con.execute("LOAD spatial;")
            
            # Create agent_movements table with spatial indexing
            self.con.execute("""
                CREATE TABLE IF NOT EXISTS agent_movements (
                    movement_id INTEGER PRIMARY KEY,
                    agent_id INTEGER NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    step_number INTEGER NOT NULL,
                    longitude DOUBLE NOT NULL,
                    latitude DOUBLE NOT NULL,
                    geometry GEOMETRY,
                    edge_id INTEGER,
                    position_along_edge DOUBLE,
                    speed DOUBLE,
                    nearby_amenities_count INTEGER DEFAULT 0
                );
            """)
            
            # Create agent_decisions table for tracking edge changes and decisions
            self.con.execute("""
                CREATE TABLE IF NOT EXISTS agent_decisions (
                    decision_id INTEGER PRIMARY KEY,
                    agent_id INTEGER NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    step_number INTEGER NOT NULL,
                    decision_type VARCHAR NOT NULL,
                    from_edge_id INTEGER,
                    to_edge_id INTEGER,
                    longitude DOUBLE NOT NULL,
                    latitude DOUBLE NOT NULL,
                    geometry GEOMETRY,
                    alternatives_count INTEGER,
                    decision_reason VARCHAR
                );
            """)
            
            # Create spatial indexes for efficient queries
            # Note: DuckDB's spatial extension uses R-tree indexes automatically when queries use spatial predicates
            
            # Create regular indexes for common queries
            self.con.execute("""
                CREATE INDEX IF NOT EXISTS idx_movements_agent_id 
                ON agent_movements(agent_id);
            """)
            
            self.con.execute("""
                CREATE INDEX IF NOT EXISTS idx_movements_timestamp 
                ON agent_movements(timestamp);
            """)
            
            self.con.execute("""
                CREATE INDEX IF NOT EXISTS idx_movements_step 
                ON agent_movements(step_number);
            """)
            
            self.con.execute("""
                CREATE INDEX IF NOT EXISTS idx_decisions_agent_id 
                ON agent_decisions(agent_id);
            """)
            
            self.con.execute("""
                CREATE INDEX IF NOT EXISTS idx_decisions_timestamp 
                ON agent_decisions(timestamp);
            """)
            
            logger.info(f"Agent tracker initialized: {self.db_path}")
            logger.info("Tables created: agent_movements, agent_decisions")
            
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise
    
    def log_movement(self, agent_id, step_number, longitude, latitude, 
                     edge_id=None, position_along_edge=None, speed=None,
                     nearby_amenities_count=0):
        """
        Log an agent's movement to the database.
        
        Args:
            agent_id: Unique identifier of the agent
            step_number: Current simulation step
            longitude: Current longitude position
            latitude: Current latitude position
            edge_id: Current edge the agent is on (optional)
            position_along_edge: Position along edge (0.0 to 1.0) (optional)
            speed: Agent's current speed (optional)
            nearby_amenities_count: Number of nearby amenities detected (optional)
        """
        try:
            timestamp = datetime.now()
            
            # Create geometry point for spatial queries
            self.con.execute("""
                INSERT INTO agent_movements 
                (movement_id, agent_id, timestamp, step_number, longitude, latitude, 
                 geometry, edge_id, position_along_edge, speed, nearby_amenities_count)
                VALUES (
                    (SELECT COALESCE(MAX(movement_id), 0) + 1 FROM agent_movements),
                    ?, ?, ?, ?, ?,
                    ST_Point(?, ?),
                    ?, ?, ?, ?
                )
            """, [agent_id, timestamp, step_number, longitude, latitude,
                  longitude, latitude, edge_id, position_along_edge, speed,
                  nearby_amenities_count])
            
        except Exception as e:
            logger.error(f"Failed to log movement for agent {agent_id}: {e}")
    
    def log_decision(self, agent_id, step_number, decision_type, longitude, latitude,
                     from_edge_id=None, to_edge_id=None, alternatives_count=None,
                     decision_reason=None):
        """
        Log an agent's decision (e.g., choosing a new edge).
        
        Args:
            agent_id: Unique identifier of the agent
            step_number: Current simulation step
            decision_type: Type of decision (e.g., 'edge_change', 'exploration', 'backtrack')
            longitude: Current longitude position where decision was made
            latitude: Current latitude position where decision was made
            from_edge_id: Edge the agent was on (optional)
            to_edge_id: Edge the agent chose to move to (optional)
            alternatives_count: Number of alternative edges available (optional)
            decision_reason: Reason for the decision (optional)
        """
        try:
            timestamp = datetime.now()
            
            self.con.execute("""
                INSERT INTO agent_decisions 
                (decision_id, agent_id, timestamp, step_number, decision_type, 
                 from_edge_id, to_edge_id, longitude, latitude, geometry,
                 alternatives_count, decision_reason)
                VALUES (
                    (SELECT COALESCE(MAX(decision_id), 0) + 1 FROM agent_decisions),
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ST_Point(?, ?),
                    ?, ?
                )
            """, [agent_id, timestamp, step_number, decision_type,
                  from_edge_id, to_edge_id, longitude, latitude,
                  longitude, latitude, alternatives_count, decision_reason])
            
        except Exception as e:
            logger.error(f"Failed to log decision for agent {agent_id}: {e}")
    
    def flush(self):
        """Ensure all data is written to disk."""
        try:
            if self.con:
                self.con.execute("CHECKPOINT;")
        except Exception as e:
            logger.error(f"Failed to flush data: {e}")
    
    def get_stats(self):
        """Get statistics about tracked data."""
        try:
            movements_count = self.con.execute(
                "SELECT COUNT(*) FROM agent_movements"
            ).fetchone()[0]
            
            decisions_count = self.con.execute(
                "SELECT COUNT(*) FROM agent_decisions"
            ).fetchone()[0]
            
            unique_agents = self.con.execute(
                "SELECT COUNT(DISTINCT agent_id) FROM agent_movements"
            ).fetchone()[0]
            
            return {
                "movements_count": movements_count,
                "decisions_count": decisions_count,
                "unique_agents": unique_agents,
                "database_path": str(self.db_path)
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {}
    
    def close(self):
        """Close the database connection."""
        try:
            if self.con:
                self.flush()
                self.con.close()
                logger.info("Agent tracker closed")
        except Exception as e:
            logger.error(f"Error closing tracker: {e}")
    
    def __del__(self):
        """Cleanup on deletion."""
        self.close()
