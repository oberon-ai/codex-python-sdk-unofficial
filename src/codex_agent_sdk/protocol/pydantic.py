"""Shared Pydantic bases for generated Codex protocol models."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Generic, Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, RootModel, TypeAdapter, ValidationError
from pydantic.main import IncEx

from ..errors import ResponseValidationError

RootValueT = TypeVar("RootValueT")
ResponseModelT = TypeVar("ResponseModelT")


class WireModel(BaseModel):
    """Base class for generated wire models.

    Generated protocol models should expose Pythonic snake_case attributes while
    continuing to accept and emit the upstream wire protocol's camelCase keys.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
    )

    def model_dump(
        self,
        *,
        mode: Literal["json", "python"] | str = "json",
        include: IncEx | None = None,
        exclude: IncEx | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool = True,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        exclude_computed_fields: bool = False,
        round_trip: bool = False,
        warnings: bool | Literal["none", "warn", "error"] = True,
        fallback: Callable[[Any], Any] | None = None,
        serialize_as_any: bool = False,
        polymorphic_serialization: bool | None = None,
    ) -> dict[str, Any]:
        """Dump a wire-ready payload by default.

        The default settings keep wire dumps compact and protocol-shaped:

        - aliases are used unless the caller opts out
        - unset optionals are omitted
        - enums and other structured values are JSON-serialized
        """

        return BaseModel.model_dump(
            self,
            mode=mode,
            include=include,
            exclude=exclude,
            context=context,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            exclude_computed_fields=exclude_computed_fields,
            round_trip=round_trip,
            warnings=warnings,
            fallback=fallback,
            serialize_as_any=serialize_as_any,
            polymorphic_serialization=polymorphic_serialization,
        )

    def model_dump_json(
        self,
        *,
        indent: int | None = None,
        ensure_ascii: bool = False,
        include: IncEx | None = None,
        exclude: IncEx | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool = True,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        exclude_computed_fields: bool = False,
        round_trip: bool = False,
        warnings: bool | Literal["none", "warn", "error"] = True,
        fallback: Callable[[Any], Any] | None = None,
        serialize_as_any: bool = False,
        polymorphic_serialization: bool | None = None,
    ) -> str:
        """Dump wire-format JSON with the same compact defaults as ``model_dump()``."""

        return BaseModel.model_dump_json(
            self,
            indent=indent,
            ensure_ascii=ensure_ascii,
            include=include,
            exclude=exclude,
            context=context,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            exclude_computed_fields=exclude_computed_fields,
            round_trip=round_trip,
            warnings=warnings,
            fallback=fallback,
            serialize_as_any=serialize_as_any,
            polymorphic_serialization=polymorphic_serialization,
        )


class WireRootModel(RootModel[RootValueT], Generic[RootValueT]):
    """RootModel counterpart to ``WireModel`` for generated union/value wrappers."""

    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
    )

    def model_dump(
        self,
        *,
        mode: Literal["json", "python"] | str = "json",
        include: IncEx | None = None,
        exclude: IncEx | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool = True,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        exclude_computed_fields: bool = False,
        round_trip: bool = False,
        warnings: bool | Literal["none", "warn", "error"] = True,
        fallback: Callable[[Any], Any] | None = None,
        serialize_as_any: bool = False,
        polymorphic_serialization: bool | None = None,
    ) -> Any:
        """Dump a wire-ready payload by default."""

        return BaseModel.model_dump(
            self,
            mode=mode,
            include=include,
            exclude=exclude,
            context=context,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            exclude_computed_fields=exclude_computed_fields,
            round_trip=round_trip,
            warnings=warnings,
            fallback=fallback,
            serialize_as_any=serialize_as_any,
            polymorphic_serialization=polymorphic_serialization,
        )

    def model_dump_json(
        self,
        *,
        indent: int | None = None,
        ensure_ascii: bool = False,
        include: IncEx | None = None,
        exclude: IncEx | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool = True,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        exclude_computed_fields: bool = False,
        round_trip: bool = False,
        warnings: bool | Literal["none", "warn", "error"] = True,
        fallback: Callable[[Any], Any] | None = None,
        serialize_as_any: bool = False,
        polymorphic_serialization: bool | None = None,
    ) -> str:
        """Dump wire-format JSON with the same compact defaults as ``model_dump()``."""

        return BaseModel.model_dump_json(
            self,
            indent=indent,
            ensure_ascii=ensure_ascii,
            include=include,
            exclude=exclude,
            context=context,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            exclude_computed_fields=exclude_computed_fields,
            round_trip=round_trip,
            warnings=warnings,
            fallback=fallback,
            serialize_as_any=serialize_as_any,
            polymorphic_serialization=polymorphic_serialization,
        )


def dump_wire_value(value: object) -> object:
    """Recursively dump BaseModel values into JSON-ready wire payloads."""

    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, Mapping):
        return {key: dump_wire_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [dump_wire_value(item) for item in value]
    if isinstance(value, tuple):
        return [dump_wire_value(item) for item in value]
    return value


def validate_response_payload(
    payload: object,
    *,
    method: str | None,
    response_model: type[ResponseModelT],
) -> ResponseModelT:
    """Validate one raw response payload against the requested response model."""

    if response_model is object:
        return cast(ResponseModelT, payload)

    try:
        validated = TypeAdapter(response_model).validate_python(payload)
    except ValidationError as exc:
        raise ResponseValidationError(
            (f"response payload failed validation against {_response_model_name(response_model)}"),
            method=method,
            payload=payload,
        ) from exc

    return validated


def _response_model_name(response_model: type[object]) -> str:
    return getattr(response_model, "__name__", repr(response_model))


__all__ = [
    "ResponseModelT",
    "WireModel",
    "WireRootModel",
    "dump_wire_value",
    "validate_response_payload",
]
