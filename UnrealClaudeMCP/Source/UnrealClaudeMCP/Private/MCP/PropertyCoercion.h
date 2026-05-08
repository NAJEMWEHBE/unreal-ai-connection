// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// PropertyCoercion - shared helper that bridges JSON values from the
// MCP layer to UE's FProperty system.
//
// v0.3.0 type list (fully supported):
//   bool, int8/16/32/64, uint8/16/32/64, float, double,
//   FString, FText, FName,
//   FVector, FVector2D, FRotator, FLinearColor, FColor,
//   UEnum-decorated enums (by value name string),
//   TSoftObjectPtr<T> (by asset path string),
//   FVector / FRotator / FLinearColor / FColor via named struct dispatch.
//
// v0.4.0 additions:
//   USTRUCT (any reflected struct) — field-by-field via recursive coercion,
//   TArray<T>  — element-wise via FScriptArrayHelper,
//   TSet<T>    — element-wise via FScriptSetHelper (auto-dedupe),
//   TMap<FString|FName, V> — string-keyed only via FScriptMapHelper,
//   FObjectProperty (hard UObject*) — asset path string -> LoadObject + class check,
//   property-name path traversal (ResolvePropertyPath).
//
// FInstancedStruct, numeric-key TMaps, and TWeakObjectPtr remain deferred.

#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonValue.h"

class FProperty;
class UObject;

namespace UCMCP::PropertyCoercion
{
    // Maximum nesting depth for recursive coercion (prevents stack overflow).
    constexpr int32 kMaxCoercionDepth = 8;

    enum class ECoerceResult : uint8
    {
        Success,
        Unsupported,        // FProperty type not in supported list
        TypeMismatch,       // JSON value type can't coerce to FProperty type
        OutOfRange,         // Numeric overflow, enum value not found, etc.
        PropertyNotFound,   // v0.4.0: segment not found during path traversal / field lookup
        Invalid,            // v0.4.0: empty path, recursion depth exceeded, etc.
    };

    struct FCoerceOutcome
    {
        ECoerceResult Result = ECoerceResult::Success;
        FString Detail;            // human-readable diagnosis (filled on failure)
        FString FPropertyClass;    // e.g., "StructProperty", "ArrayProperty"
                                   // (filled on Unsupported, for the error msg)
    };

    // v0.4.0: result of ResolvePropertyPath — identifies the target property and its
    // containing memory so callers can pass all three to SetProperty / EncodeProperty.
    struct FResolvedProperty
    {
        FProperty* Property   = nullptr;
        void*      Container  = nullptr;  // The UObject or struct memory the property lives in
        void*      PropAddr   = nullptr;  // Address of the property value within Container
        FString    ResolvedPath;          // Full dotted path that was resolved (for error messages)
    };

    /**
     * Resolve a dotted property path (e.g. "RootComponent.RelativeLocation") starting
     * from RootObject. Each segment is looked up as a UPROPERTY on the current class;
     * FObjectProperty segments are dereferenced, FStructProperty segments step into the
     * struct memory. Returns Success and fills OutResolved on success.
     */
    FCoerceOutcome ResolvePropertyPath(
        UObject* RootObject,
        const FString& DottedPath,
        FResolvedProperty& OutResolved
    );

    /**
     * Coerce a JSON value into the given FProperty.
     * PropAddr must be a valid pointer into Container (or any suitable memory for the type).
     * Container is only used for error context — it is not mutated directly by the coercion
     * for top-level calls, but recursive USTRUCT calls use it to find sub-property addresses.
     *
     * CurrentPath and Depth are used internally for recursive calls; callers at the handler
     * level can leave them at their defaults.
     */
    FCoerceOutcome SetProperty(
        UObject* Container,
        FProperty* Property,
        void* PropAddr,
        const TSharedPtr<FJsonValue>& Value,
        const FString& CurrentPath = TEXT("."),
        int32 Depth = 0
    );

    /**
     * Read the current value of a property at PropAddr and encode it as a JSON value.
     * Used to return old_value / new_value in set_actor_property's success response,
     * and for recursive ENCODE of nested types.
     */
    TSharedPtr<FJsonValue> EncodeProperty(
        FProperty* Property,
        const void* PropAddr,
        const FString& CurrentPath = TEXT("."),
        int32 Depth = 0
    );

    // ---------------------------------------------------------------------------
    // Legacy compatibility wrappers — kept so existing callers compile unchanged.
    // Both delegate to the new 6-arg / 4-arg overloads above.
    // ---------------------------------------------------------------------------

    /**
     * Coerce a JSON value into the given FProperty on the target UObject.
     * (v0.3.0 signature — delegates to the new SetProperty overload.)
     */
    inline FCoerceOutcome SetProperty(
        UObject* Target,
        FProperty* Property,
        const TSharedPtr<FJsonValue>& Value)
    {
        if (!Target || !Property || !Value.IsValid())
            return FCoerceOutcome{ ECoerceResult::TypeMismatch, TEXT("null target/property/value") };
        void* PropAddr = Property->ContainerPtrToValuePtr<void>(Target);
        return SetProperty(Target, Property, PropAddr, Value, TEXT("."), 0);
    }

    /**
     * Read the current value of a property from Target and encode it as JSON.
     * (v0.3.0 signature — delegates to EncodeProperty.)
     */
    TSharedPtr<FJsonValue> GetProperty(
        UObject* Target,
        FProperty* Property);

    /**
     * Find a property by name on the object's class. Returns null if no property matches.
     * (Convenience wrapper — unchanged from v0.3.0.)
     */
    FProperty* FindProperty(UObject* Target, const FString& PropertyName);
}
