from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

# Create SQLite database engine
DATABASE_URL = "sqlite:///voice_agent.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class CallRecord(Base):
    """Model for storing call records in SQLite"""
    __tablename__ = "call_records"
    
    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(String, unique=True, index=True)
    phone_number = Column(String, index=True)
    direction = Column(String)  # "inbound" or "outbound"
    start_time = Column(DateTime, default=datetime.now)
    end_time = Column(DateTime, nullable=True)
    status = Column(String)  # "initiated", "in-progress", "completed", "failed"
    transcript = Column(Text, default="")
    intent = Column(String, nullable=True)
    
def init_db():
    """Initialize the database by creating all tables"""
    Base.metadata.create_all(bind=engine)
    
def get_db():
    """Get a database session"""
    db = SessionLocal()
    try:
        return db
    finally:
        db.close()