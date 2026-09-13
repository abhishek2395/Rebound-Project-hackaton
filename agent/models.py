"""
Rebound Data Models
Core schemas for traveler profiles, disruption events, Duffel flight offers,
decisions, and audit traces using Pydantic v2.
"""

from datetime import datetime
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class DisruptionType(str, Enum):
    CANCELLED = "cancelled"
    DELAYED = "delayed"
    MISSED_CONNECTION = "missed_connection"


class ActionType(str, Enum):
    BOOK = "book"
    ASK = "ask"
    ASK_THEN_BOOK = "ask_then_book"
    ESCALATE = "escalate"
    NOTIFY_ONLY = "notify_only"
    SKIPPED_DUPLICATE = "skipped_duplicate"


class CabinClass(str, Enum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class FlightDetails(BaseModel):
    carrier: str
    number: str
    origin: str
    destination: str
    scheduled_departure: datetime
    scheduled_arrival: datetime


class DisruptionEvent(BaseModel):
    event_id: str
    type: DisruptionType
    order_id: Optional[str] = None
    flight: FlightDetails
    delay_minutes: Optional[int] = None
    reason: Optional[str] = "operational"
    timestamp: Optional[datetime] = None

    @field_validator("event_id")
    def validate_event_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("event_id cannot be empty")
        return v.strip()


class Preferences(BaseModel):
    preferred_airlines: List[str] = Field(default_factory=lambda: ["ZZ"])
    max_layovers: int = 1
    seat: str = "aisle"
    cabin: CabinClass = CabinClass.ECONOMY
    avoid_redeye: bool = True


class Mandate(BaseModel):
    spend_ceiling_usd: float = 800.0
    approval_threshold_usd: float = 300.0
    auto_book: bool = True


class Contact(BaseModel):
    name: str
    relation: str = "pickup"
    phone: str
    email: Optional[str] = None


class TravelerProfile(BaseModel):
    traveler_id: str
    name: str
    email: str
    phone: str
    preferences: Preferences = Field(default_factory=Preferences)
    mandate: Mandate = Field(default_factory=Mandate)
    contacts: List[Contact] = Field(default_factory=list)
    default_deadline_buffer_hours: float = 4.0


class FlightOffer(BaseModel):
    id: str
    carrier: str = "ZZ"
    flight_number: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    departs_at: datetime
    arrives_at: datetime
    total_amount: float
    currency: str = "USD"
    segments: int = 1
    cabin_class: CabinClass = CabinClass.ECONOMY
    raw_payload: Optional[Dict[str, Any]] = None

    @property
    def layovers(self) -> int:
        return max(0, self.segments - 1)

    @property
    def is_redeye(self) -> bool:
        """Departure between 22:00 and 05:00 local/departure time."""
        hour = self.departs_at.hour
        return hour >= 22 or hour < 5


class DecisionRecord(BaseModel):
    event_id: str
    action: ActionType
    chosen_offer_id: Optional[str] = None
    cost_delta_usd: float = 0.0
    arrives_at: Optional[datetime] = None
    deadline: Optional[datetime] = None
    reasoning: str = ""
    alternatives: List[str] = Field(default_factory=list)


class TraceStatus(str, Enum):
    OK = "ok"
    RETRY = "retry"
    ERROR = "error"
    SKIPPED = "skipped"


class TraceEntry(BaseModel):
    run_id: str
    step: str
    tool: str
    input: Dict[str, Any] = Field(default_factory=dict)
    output_summary: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    status: TraceStatus = TraceStatus.OK
    ts: datetime = Field(default_factory=datetime.utcnow)
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HoldOrder(BaseModel):
    order_id: str
    offer_id: str
    total_amount: float
    currency: str = "USD"
    price_guarantee_expires_at: Optional[datetime] = None
    status: str = "hold"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConfirmedBooking(BaseModel):
    order_id: str
    booking_reference: str
    offer_id: str
    total_amount: float
    currency: str = "USD"
    confirmed_at: datetime = Field(default_factory=datetime.utcnow)
    confirmed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    passenger_name: str
    passenger_email: str


class CancellationQuote(BaseModel):
    order_id: str
    cancellation_id: str
    refund_amount: float
    refund_currency: str = "USD"
    expires_at: Optional[datetime] = None


class CalendarEvent(BaseModel):
    event_id: str
    summary: str
    start: datetime
    end: datetime
    description: Optional[str] = None
    duffel_order_id: Optional[str] = None
    is_meeting: bool = False

