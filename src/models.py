from pydantic import BaseModel, Field
from typing import Optional


class TrailerFilter(BaseModel):
    """
    Metadata filter model — fields mirror Pinecone metadata.
    All optional; only populated fields are used for filtering.
    """
    condition: Optional[str] = Field(None, description="'New' or 'Pre-Owned'")
    price_min: Optional[float] = Field(None, description="Minimum price in USD")
    price_max: Optional[float] = Field(None, description="Maximum price in USD")
    category_subcategory: Optional[str] = Field(
        None,
        description="Category, e.g. 'Utility', 'Enclosed', 'Dump', 'Flatbed', 'Equipment', 'Livestock', 'Tilt', 'Aluminum', 'Car Hauler'"
    )
    make: Optional[str] = Field(None, description="Trailer manufacturer brand")
    color: Optional[str] = Field(None, description="Trailer color")
    hitch_type: Optional[str] = Field(None, description="'Bumper Pull' or 'Gooseneck'")


class TrailerListing(BaseModel):
    listing_id: str
    title: str
    condition: str
    price: Optional[float]
    price_display: Optional[str] = None
    payments_from: Optional[str]
    category_subcategory: str
    make: str
    color: str
    hitch_type: Optional[str]
    year: Optional[str]
    length: Optional[str]
    width: Optional[str]
    axles: Optional[str]
    gvwr: Optional[str]
    payload_capacity: Optional[str]
    trailer_material: Optional[str]
    floor: Optional[str]
    url: str
    score: Optional[float] = None
