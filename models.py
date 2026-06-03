import enum
from sqlalchemy import Column, Integer, String, Float, Text, Enum, Date
from database import Base

class LoadStatus(str, enum.Enum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    DELIVERED = "Delivered"
    INVOICED = "Invoiced"
    PAID = "Paid"

class Load(Base):
    __tablename__ = "loads"

    id = Column(Integer, primary_key=True, index=True)
    
    # Broker Info
    broker_name = Column(String, index=True)
    broker_email = Column(String, nullable=True) # Useful for sending the invoice later
    load_reference = Column(String, index=True)  # Broker's Load ID / PO Number
    
    # Load Details
    rate_amount = Column(Float, nullable=False)
    pickup_location = Column(String, nullable=False)   # e.g., "Chicago, IL"
    pickup_date = Column(Date, nullable=False)
    delivery_location = Column(String, nullable=False) # e.g., "Dallas, TX"
    delivery_date = Column(Date, nullable=False)
    
    # Status
    status = Column(Enum(LoadStatus), default=LoadStatus.DRAFT)
    
    # Extras
    notes = Column(Text, nullable=True) # Pickup numbers, gate codes, etc.
    
    # File storage (saving the paths to where the files are stored on your server/computer)
    rate_con_pdf_path = Column(String, nullable=True)
    bol_pdf_path = Column(String, nullable=True)