from decimal import Decimal

from pydantic import BaseModel, field_serializer, field_validator


class APIModel(BaseModel):
    """Serialization and schema extensions; every DTO field comes from OpenAPI."""

    @field_validator("*", check_fields=False)
    @classmethod
    def validate_extensions(cls, value, info):
        extra = cls.model_fields[info.field_name].json_schema_extra or {}
        if unique_by := extra.get("x-unique-by"):
            values = [getattr(item, unique_by) for item in value]
            if len(values) != len(set(values)):
                raise ValueError(f"{unique_by} must not repeat")
        if after := extra.get("x-after"):
            if after in info.data and value <= info.data[after]:
                raise ValueError(f"Must be later than {after}")
        return value

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_decimal(self, value):
        if isinstance(value, Decimal):
            return float(value)
        return value
