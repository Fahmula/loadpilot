import enum
from sqlalchemy import Column, Integer, String, Float, Text, Enum, Date, Boolean
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

    invoice_number = Column(String, unique=True, index=True, nullable=True)
    
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
    loaded_miles = Column(Integer, nullable=False, default=0)
    empty_miles = Column(Integer, nullable=False, default=0)
    
    # Status
    status = Column(Enum(LoadStatus), default=LoadStatus.DRAFT)
    
    # Extras
    notes = Column(Text, nullable=True) # Pickup numbers, gate codes, etc.
    
    # File storage
    rate_con_pdf_path = Column(String, nullable=True)
    bol_pdf_path = Column(String, nullable=True)

class CompanySettings(Base):
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True, index=True, default=1)
    
    # Company Info
    company_name = Column(String, nullable=True)
    street_address = Column(String, nullable=True)
    city_state_zip = Column(String, nullable=True)
    show_address_on_invoice = Column(Boolean, default=True)
    
    # Banking Info (Optional)
    bank_name = Column(String, nullable=True)
    account_number = Column(String, nullable=True)
    routing_number = Column(String, nullable=True)
    show_bank_on_invoice = Column(Boolean, default=False)