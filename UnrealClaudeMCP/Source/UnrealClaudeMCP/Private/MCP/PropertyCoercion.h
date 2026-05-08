// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// PropertyCoercion - shared helper that bridges JSON values from the
// MCP layer to UE's FProperty system. Supports the v0.3.0 type list:
//
//   bool, int8/16/32/64, uint8/16/32/64, float, double,
//   FString, FText, FName,
//   FVector, FVector2D, FRotator, FLinearColor, FColor,
//   UEnum-decorated enums (by value name string),
//   TSoftObjectPtr<T> (by asset path string).
//
// USTRUCT (custom structs), TArray, TMap, TSet, FInstancedStruct, and
// hard UObject* references are deferred to v0.4.0 — calling these
// helpers on those types returns ECoerceResult::Unsupported.

#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonValue.h"

class FProperty;

namespace UCMCP::PropertyCoercion
{
    enum class ECoerceResult : uint8
    {
        Success,
        Unsupported,    // FProperty type not in v0.3.0 supported list
        TypeMismatch,   // JSON value type can't coerce to FProperty type
                        // (e.g., string for an int)
        OutOfRange,     // Numeric overflow, enum value not found, etc.
    };

    struct FCoerceOutcome
    {
        ECoerceResult Result = ECoerceResult::Success;
        FString Detail;            // human-readable diagnosis (filled on failure)
        FString FPropertyClass;    // e.g., "StructProperty", "ArrayProperty"
                                   // (filled on Unsupported, for the error msg)
    };

    /**
     * Coerce a JSON value into the given FProperty on the target object.
     * The target object's memory is mutated in-place.
     */
    FCoerceOutcome SetProperty(
        UObject* Target,
        FProperty* Property,
        const TSharedPtr<FJsonValue>& Value);

    /**
     * Read the current value of a property from the target object and
     * encode it as a JSON value. Used to return old_value/new_value
     * in set_actor_property's success response.
     */
    TSharedPtr<FJsonValue> GetProperty(
        UObject* Target,
        FProperty* Property);

    /**
     * Find a property by name on the object's class. Returns null if
     * no property matches. (Convenience wrapper.)
     */
    FProperty* FindProperty(UObject* Target, const FString& PropertyName);
}
