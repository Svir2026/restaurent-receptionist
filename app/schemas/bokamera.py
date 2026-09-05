from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class BokaMeraModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class BokaMeraService(BokaMeraModel):
    service_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    duration_minutes: int | None = Field(default=None, gt=0)
    active: bool


class BokaMeraAvailableTime(BokaMeraModel):
    starts_at: datetime
    ends_at: datetime
    free_spots: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_interval(self) -> "BokaMeraAvailableTime":
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("BokaMera times must include a timezone")
        if self.ends_at <= self.starts_at:
            raise ValueError("BokaMera end time must be after start time")
        return self


class BokaMeraCustomer(BokaMeraModel):
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=5, max_length=32)
    email: EmailStr | None = None


class BokaMeraResource(BokaMeraModel):
    resource_type_id: int = Field(gt=0)
    resource_id: int = Field(gt=0)


class BokaMeraCustomField(BokaMeraModel):
    field_id: int = Field(gt=0)
    value: str = Field(min_length=1, max_length=1000)


class CreateBokaMeraBookingRequest(BokaMeraModel):
    service_id: int = Field(gt=0)
    starts_at: datetime
    ends_at: datetime
    customer: BokaMeraCustomer
    booked_comments: str = Field(default="", max_length=4000)
    resources: list[BokaMeraResource] = Field(default_factory=list)
    custom_fields: list[BokaMeraCustomField] = Field(default_factory=list)
    price_id: int = Field(default=0, ge=0)
    send_sms_confirmation: bool = True
    send_email_confirmation: bool = True

    @model_validator(mode="after")
    def validate_booking(self) -> "CreateBokaMeraBookingRequest":
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("BokaMera booking times must include a timezone")
        if self.ends_at <= self.starts_at:
            raise ValueError("BokaMera booking end must be after its start")
        if self.send_email_confirmation and self.customer.email is None:
            raise ValueError(
                "customer email is required when email confirmation is enabled"
            )
        return self


class BokaMeraBooking(BokaMeraModel):
    booking_id: int = Field(gt=0)
    service_id: int = Field(gt=0)
    starts_at: datetime
    ends_at: datetime
    status: str = Field(min_length=1, max_length=100)

