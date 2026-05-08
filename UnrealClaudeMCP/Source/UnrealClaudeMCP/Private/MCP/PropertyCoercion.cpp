// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/PropertyCoercion.h"

#include "Dom/JsonObject.h"
#include "UObject/UnrealType.h"
#include "UObject/EnumProperty.h"
#include "UObject/TextProperty.h"
#include "UObject/UObjectGlobals.h"
#include "Math/Color.h"
#include "Math/Vector.h"
#include "Math/Vector2D.h"
#include "Math/Rotator.h"

// FScriptArrayHelper / FScriptSetHelper / FScriptMapHelper live in UnrealType.h
// (verified against UE 5.7 source — all in the same TU via the include above).

namespace UCMCP::PropertyCoercion
{
    // -----------------------------------------------------------------------
    // Internal helpers
    // -----------------------------------------------------------------------

    static FCoerceOutcome MakeOutcome(ECoerceResult R, const FString& Detail = FString(), const FString& Cls = FString())
    {
        return FCoerceOutcome{ R, Detail, Cls };
    }

    static FString ChildPath(const FString& Parent, const FString& Field)
    {
        return (Parent == TEXT(".")) ? (TEXT(".") + Field) : (Parent + TEXT(".") + Field);
    }

    static FString IndexedPath(const FString& Parent, int32 Index)
    {
        return FString::Printf(TEXT("%s[%d]"), *Parent, Index);
    }

    // Struct-field helpers (v0.3.0 named-struct fast paths).
    static bool ReadVector(const TSharedPtr<FJsonObject>& Obj, FVector& Out)
    {
        if (!Obj.IsValid()) return false;
        double X, Y, Z;
        if (!Obj->TryGetNumberField(TEXT("x"), X)) return false;
        if (!Obj->TryGetNumberField(TEXT("y"), Y)) return false;
        if (!Obj->TryGetNumberField(TEXT("z"), Z)) return false;
        Out = FVector(X, Y, Z);
        return true;
    }

    static bool ReadVector2D(const TSharedPtr<FJsonObject>& Obj, FVector2D& Out)
    {
        if (!Obj.IsValid()) return false;
        double X, Y;
        if (!Obj->TryGetNumberField(TEXT("x"), X)) return false;
        if (!Obj->TryGetNumberField(TEXT("y"), Y)) return false;
        Out = FVector2D(X, Y);
        return true;
    }

    static bool ReadRotator(const TSharedPtr<FJsonObject>& Obj, FRotator& Out)
    {
        if (!Obj.IsValid()) return false;
        double Pitch, Yaw, Roll;
        if (!Obj->TryGetNumberField(TEXT("pitch"), Pitch)) return false;
        if (!Obj->TryGetNumberField(TEXT("yaw"), Yaw)) return false;
        if (!Obj->TryGetNumberField(TEXT("roll"), Roll)) return false;
        Out = FRotator(Pitch, Yaw, Roll);
        return true;
    }

    static bool ReadLinearColor(const TSharedPtr<FJsonObject>& Obj, FLinearColor& Out)
    {
        if (!Obj.IsValid()) return false;
        double R, G, B, A;
        if (!Obj->TryGetNumberField(TEXT("r"), R)) return false;
        if (!Obj->TryGetNumberField(TEXT("g"), G)) return false;
        if (!Obj->TryGetNumberField(TEXT("b"), B)) return false;
        if (!Obj->TryGetNumberField(TEXT("a"), A)) A = 1.0;
        Out = FLinearColor(R, G, B, A);
        return true;
    }

    static bool ReadColor(const TSharedPtr<FJsonObject>& Obj, FColor& Out)
    {
        if (!Obj.IsValid()) return false;
        double R, G, B, A;
        if (!Obj->TryGetNumberField(TEXT("r"), R)) return false;
        if (!Obj->TryGetNumberField(TEXT("g"), G)) return false;
        if (!Obj->TryGetNumberField(TEXT("b"), B)) return false;
        if (!Obj->TryGetNumberField(TEXT("a"), A)) A = 255.0;
        Out = FColor((uint8)FMath::Clamp(R, 0.0, 255.0),
                     (uint8)FMath::Clamp(G, 0.0, 255.0),
                     (uint8)FMath::Clamp(B, 0.0, 255.0),
                     (uint8)FMath::Clamp(A, 0.0, 255.0));
        return true;
    }

    // -----------------------------------------------------------------------
    // Step 2: ResolvePropertyPath
    // -----------------------------------------------------------------------

    FCoerceOutcome ResolvePropertyPath(UObject* RootObject, const FString& DottedPath, FResolvedProperty& OutResolved)
    {
        if (!RootObject)
        {
            return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("ResolvePropertyPath: null root object"));
        }

        TArray<FString> Segments;
        DottedPath.ParseIntoArray(Segments, TEXT("."), /*CullEmpty=*/true);
        if (Segments.Num() == 0)
        {
            return MakeOutcome(ECoerceResult::Invalid, TEXT("empty property path"));
        }

        UStruct* CurrentStruct = RootObject->GetClass();
        void* CurrentContainer = RootObject;
        FString PathSoFar;

        for (int32 i = 0; i < Segments.Num(); ++i)
        {
            const FString& Segment = Segments[i];
            PathSoFar = PathSoFar.IsEmpty() ? Segment : PathSoFar + TEXT(".") + Segment;

            FProperty* Prop = CurrentStruct->FindPropertyByName(FName(*Segment));
            if (!Prop)
            {
                return MakeOutcome(
                    ECoerceResult::PropertyNotFound,
                    FString::Printf(TEXT("property_not_found at .%s: '%s' on %s"),
                        *PathSoFar, *Segment, *CurrentStruct->GetName())
                );
            }

            void* PropAddr = Prop->ContainerPtrToValuePtr<void>(CurrentContainer);

            if (i == Segments.Num() - 1)
            {
                // Last segment — this is the target property.
                OutResolved.Property     = Prop;
                OutResolved.Container    = CurrentContainer;
                OutResolved.PropAddr     = PropAddr;
                OutResolved.ResolvedPath = PathSoFar;
                return MakeOutcome(ECoerceResult::Success);
            }

            // Not the last segment — must be traversable.
            if (FStructProperty* StructProp = CastField<FStructProperty>(Prop))
            {
                CurrentStruct    = StructProp->Struct;
                CurrentContainer = PropAddr;
            }
            else if (FObjectProperty* ObjProp = CastField<FObjectProperty>(Prop))
            {
                UObject* Pointed = ObjProp->GetObjectPropertyValue(PropAddr);
                if (!Pointed)
                {
                    return MakeOutcome(
                        ECoerceResult::TypeMismatch,
                        FString::Printf(TEXT("path_traversal_null at .%s: cannot continue through null UObject"), *PathSoFar)
                    );
                }
                CurrentStruct    = Pointed->GetClass();
                CurrentContainer = Pointed;
            }
            else
            {
                return MakeOutcome(
                    ECoerceResult::Unsupported,
                    FString::Printf(TEXT("path_traversal_invalid_type at .%s: cannot traverse through %s"),
                        *PathSoFar, *Prop->GetClass()->GetName())
                );
            }
        }

        // Unreachable — Segments.Num() == 0 is handled above.
        return MakeOutcome(ECoerceResult::Invalid, TEXT("unreachable"));
    }

    // -----------------------------------------------------------------------
    // Step 1 + Steps 3-10: SetProperty (new 6-arg overload)
    // -----------------------------------------------------------------------

    FCoerceOutcome SetProperty(
        UObject* Container,
        FProperty* Property,
        void* PropAddr,
        const TSharedPtr<FJsonValue>& Value,
        const FString& CurrentPath,
        int32 Depth)
    {
        // Step 3: Depth guard.
        if (Depth >= kMaxCoercionDepth)
        {
            return MakeOutcome(
                ECoerceResult::Invalid,
                FString::Printf(TEXT("recursion_depth_exceeded at %s (max %d)"), *CurrentPath, kMaxCoercionDepth)
            );
        }

        if (!Property || !Value.IsValid())
        {
            return MakeOutcome(ECoerceResult::TypeMismatch,
                FString::Printf(TEXT("null property/value at %s"), *CurrentPath));
        }

        // Step 10: Bool — path-prefixed error messages.
        if (FBoolProperty* BoolProp = CastField<FBoolProperty>(Property))
        {
            bool B;
            if (!Value->TryGetBool(B))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected bool at %s"), *CurrentPath));
            }
            BoolProp->SetPropertyValue(PropAddr, B);
            return MakeOutcome(ECoerceResult::Success);
        }

        // Step 10: Numeric.
        if (FNumericProperty* NumProp = CastField<FNumericProperty>(Property))
        {
            double D;
            if (!Value->TryGetNumber(D))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected number at %s"), *CurrentPath));
            }
            if (NumProp->IsFloatingPoint())
            {
                NumProp->SetFloatingPointPropertyValue(PropAddr, D);
            }
            else
            {
                NumProp->SetIntPropertyValue(PropAddr, (int64)D);
            }
            return MakeOutcome(ECoerceResult::Success);
        }

        // Step 10: FString.
        if (FStrProperty* StrProp = CastField<FStrProperty>(Property))
        {
            FString S;
            if (!Value->TryGetString(S))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected string at %s"), *CurrentPath));
            }
            StrProp->SetPropertyValue(PropAddr, S);
            return MakeOutcome(ECoerceResult::Success);
        }

        // Step 10: FName.
        if (FNameProperty* NameProp = CastField<FNameProperty>(Property))
        {
            FString S;
            if (!Value->TryGetString(S))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected string at %s"), *CurrentPath));
            }
            NameProp->SetPropertyValue(PropAddr, FName(*S));
            return MakeOutcome(ECoerceResult::Success);
        }

        // Step 10: FText.
        if (FTextProperty* TextProp = CastField<FTextProperty>(Property))
        {
            FString S;
            if (!Value->TryGetString(S))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected string at %s"), *CurrentPath));
            }
            TextProp->SetPropertyValue(PropAddr, FText::FromString(S));
            return MakeOutcome(ECoerceResult::Success);
        }

        // Step 10: Enum.
        if (FEnumProperty* EnumProp = CastField<FEnumProperty>(Property))
        {
            FString S;
            if (!Value->TryGetString(S))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected enum value name string at %s"), *CurrentPath));
            }
            UEnum* Enum = EnumProp->GetEnum();
            const int64 EnumVal = Enum->GetValueByNameString(S);
            if (EnumVal == INDEX_NONE)
            {
                return MakeOutcome(ECoerceResult::OutOfRange,
                    FString::Printf(TEXT("enum '%s' has no value named '%s' at %s"),
                        *Enum->GetName(), *S, *CurrentPath));
            }
            EnumProp->GetUnderlyingProperty()->SetIntPropertyValue(PropAddr, EnumVal);
            return MakeOutcome(ECoerceResult::Success);
        }

        // Step 4: USTRUCT branch.
        // Handles: well-known UE structs (FVector, FRotator, FLinearColor, FColor, FVector2D)
        // via the v0.3.0 named dispatch, then arbitrary USTRUCT via generic recursive field walk.
        if (FStructProperty* StructProp = CastField<FStructProperty>(Property))
        {
            UScriptStruct* Struct = StructProp->Struct;
            const TSharedPtr<FJsonObject>* ObjPtr = nullptr;
            if (!Value->TryGetObject(ObjPtr))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected JSON object for struct at %s"), *CurrentPath));
            }
            const TSharedPtr<FJsonObject>& Obj = *ObjPtr;

            // v0.3.0 named-struct fast paths (kept for speed + backward compat).
            if (Struct == TBaseStructure<FVector>::Get())
            {
                FVector V;
                if (!ReadVector(Obj, V))
                    return MakeOutcome(ECoerceResult::TypeMismatch,
                        FString::Printf(TEXT("expected {x,y,z} at %s"), *CurrentPath));
                *(FVector*)PropAddr = V;
                return MakeOutcome(ECoerceResult::Success);
            }
            if (Struct == TBaseStructure<FVector2D>::Get())
            {
                FVector2D V;
                if (!ReadVector2D(Obj, V))
                    return MakeOutcome(ECoerceResult::TypeMismatch,
                        FString::Printf(TEXT("expected {x,y} at %s"), *CurrentPath));
                *(FVector2D*)PropAddr = V;
                return MakeOutcome(ECoerceResult::Success);
            }
            if (Struct == TBaseStructure<FRotator>::Get())
            {
                FRotator R;
                if (!ReadRotator(Obj, R))
                    return MakeOutcome(ECoerceResult::TypeMismatch,
                        FString::Printf(TEXT("expected {pitch,yaw,roll} at %s"), *CurrentPath));
                *(FRotator*)PropAddr = R;
                return MakeOutcome(ECoerceResult::Success);
            }
            if (Struct == TBaseStructure<FLinearColor>::Get())
            {
                FLinearColor C;
                if (!ReadLinearColor(Obj, C))
                    return MakeOutcome(ECoerceResult::TypeMismatch,
                        FString::Printf(TEXT("expected {r,g,b,a} at %s"), *CurrentPath));
                *(FLinearColor*)PropAddr = C;
                return MakeOutcome(ECoerceResult::Success);
            }
            if (Struct == TBaseStructure<FColor>::Get())
            {
                FColor C;
                if (!ReadColor(Obj, C))
                    return MakeOutcome(ECoerceResult::TypeMismatch,
                        FString::Printf(TEXT("expected {r,g,b,a} at %s"), *CurrentPath));
                *(FColor*)PropAddr = C;
                return MakeOutcome(ECoerceResult::Success);
            }

            // Generic USTRUCT: field-by-field recursive coercion.
            for (const auto& Pair : Obj->Values)
            {
                const FString& FieldName = Pair.Key;
                FProperty* FieldProp = Struct->FindPropertyByName(FName(*FieldName));
                if (!FieldProp)
                {
                    return MakeOutcome(ECoerceResult::PropertyNotFound,
                        FString::Printf(TEXT("field_not_found at %s.%s: '%s' on %s"),
                            *CurrentPath, *FieldName, *FieldName, *Struct->GetName()));
                }
                void* FieldAddr = FieldProp->ContainerPtrToValuePtr<void>(PropAddr);
                const FString FieldPath = ChildPath(CurrentPath, FieldName);
                FCoerceOutcome FieldOutcome = SetProperty(Container, FieldProp, FieldAddr, Pair.Value, FieldPath, Depth + 1);
                if (FieldOutcome.Result != ECoerceResult::Success)
                {
                    return FieldOutcome;  // Bubble up path-prefixed error.
                }
            }
            return MakeOutcome(ECoerceResult::Success);
        }

        // Step 5: TArray branch.
        if (FArrayProperty* ArrayProp = CastField<FArrayProperty>(Property))
        {
            const TArray<TSharedPtr<FJsonValue>>* AsArr = nullptr;
            if (!Value->TryGetArray(AsArr))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected JSON array for TArray at %s"), *CurrentPath));
            }

            FScriptArrayHelper Helper(ArrayProp, PropAddr);
            Helper.EmptyAndAddUninitializedValues(AsArr->Num());
            for (int32 i = 0; i < AsArr->Num(); ++i)
            {
                void* ElemAddr = Helper.GetRawPtr(i);
                // Default-construct each element before coercion (EmptyAndAddUninitializedValues
                // leaves memory uninitialized; inner property may need zero-init).
                ArrayProp->Inner->InitializeValue(ElemAddr);
                const FString ElemPath = IndexedPath(CurrentPath, i);
                FCoerceOutcome ElemOutcome = SetProperty(Container, ArrayProp->Inner, ElemAddr, (*AsArr)[i], ElemPath, Depth + 1);
                if (ElemOutcome.Result != ECoerceResult::Success)
                {
                    return ElemOutcome;
                }
            }
            return MakeOutcome(ECoerceResult::Success);
        }

        // Step 6: TSet branch.
        if (FSetProperty* SetProp = CastField<FSetProperty>(Property))
        {
            const TArray<TSharedPtr<FJsonValue>>* AsArr = nullptr;
            if (!Value->TryGetArray(AsArr))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected JSON array for TSet at %s"), *CurrentPath));
            }

            FScriptSetHelper Helper(SetProp, PropAddr);
            Helper.EmptyElements(AsArr->Num());
            for (int32 i = 0; i < AsArr->Num(); ++i)
            {
                const int32 NewIndex = Helper.AddDefaultValue_Invalid_NeedsRehash();
                void* ElemAddr = Helper.GetElementPtr(NewIndex);
                const FString ElemPath = IndexedPath(CurrentPath, i);
                FCoerceOutcome ElemOutcome = SetProperty(Container, SetProp->ElementProp, ElemAddr, (*AsArr)[i], ElemPath, Depth + 1);
                if (ElemOutcome.Result != ECoerceResult::Success)
                {
                    return ElemOutcome;
                }
            }
            Helper.Rehash();
            return MakeOutcome(ECoerceResult::Success);
        }

        // Step 7: TMap branch (string/name keys only).
        if (FMapProperty* MapProp = CastField<FMapProperty>(Property))
        {
            const bool bStrKey  = (CastField<FStrProperty>(MapProp->KeyProp)  != nullptr);
            const bool bNameKey = (CastField<FNameProperty>(MapProp->KeyProp) != nullptr);
            if (!bStrKey && !bNameKey)
            {
                return MakeOutcome(ECoerceResult::Unsupported,
                    FString::Printf(TEXT("unsupported_property_type at %s: TMap with non-string key (got %s)"),
                        *CurrentPath, *MapProp->KeyProp->GetClass()->GetName()));
            }

            const TSharedPtr<FJsonObject>* AsObj = nullptr;
            if (!Value->TryGetObject(AsObj))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected JSON object for TMap at %s"), *CurrentPath));
            }

            FScriptMapHelper Helper(MapProp, PropAddr);
            Helper.EmptyValues();
            for (const auto& Pair : (*AsObj)->Values)
            {
                const int32 NewIndex = Helper.AddDefaultValue_Invalid_NeedsRehash();
                void* KeyAddr = Helper.GetKeyPtr(NewIndex);
                void* ValAddr = Helper.GetValuePtr(NewIndex);

                // Set key (string or name).
                if (bStrKey)
                {
                    CastFieldChecked<FStrProperty>(MapProp->KeyProp)->SetPropertyValue(KeyAddr, Pair.Key);
                }
                else
                {
                    CastFieldChecked<FNameProperty>(MapProp->KeyProp)->SetPropertyValue(KeyAddr, FName(*Pair.Key));
                }

                // Coerce value recursively.
                const FString ValPath = ChildPath(CurrentPath, Pair.Key);
                FCoerceOutcome ValOutcome = SetProperty(Container, MapProp->ValueProp, ValAddr, Pair.Value, ValPath, Depth + 1);
                if (ValOutcome.Result != ECoerceResult::Success)
                {
                    return ValOutcome;
                }
            }
            Helper.Rehash();
            return MakeOutcome(ECoerceResult::Success);
        }

        // Step 8: FObjectProperty (hard UObject* pointer).
        if (FObjectProperty* ObjProp = CastField<FObjectProperty>(Property))
        {
            FString S;
            if (!Value->TryGetString(S))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected asset path string at %s"), *CurrentPath));
            }
            if (S.IsEmpty())
            {
                ObjProp->SetObjectPropertyValue(PropAddr, nullptr);
                return MakeOutcome(ECoerceResult::Success);
            }
            UObject* Loaded = LoadObject<UObject>(nullptr, *S);
            if (!Loaded)
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("asset_not_found at %s: '%s'"), *CurrentPath, *S));
            }
            if (ObjProp->PropertyClass && !Loaded->IsA(ObjProp->PropertyClass))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("wrong_object_class at %s: expected %s, got %s"),
                        *CurrentPath,
                        *ObjProp->PropertyClass->GetName(),
                        *Loaded->GetClass()->GetName()));
            }
            ObjProp->SetObjectPropertyValue(PropAddr, Loaded);
            return MakeOutcome(ECoerceResult::Success);
        }

        // TSoftObjectPtr<T>.
        if (FSoftObjectProperty* SoftProp = CastField<FSoftObjectProperty>(Property))
        {
            FString S;
            if (!Value->TryGetString(S))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch,
                    FString::Printf(TEXT("expected asset path string at %s"), *CurrentPath));
            }
            FSoftObjectPath Path(S);
            SoftProp->SetPropertyValue(PropAddr, FSoftObjectPtr(Path));
            return MakeOutcome(ECoerceResult::Success);
        }

        // Anything else (FInstancedStruct, TWeakObjectPtr, numeric-key TMap, etc.)
        return MakeOutcome(ECoerceResult::Unsupported,
            FString::Printf(TEXT("unsupported_property_type at %s: FProperty class '%s' not in supported list"),
                *CurrentPath, *Property->GetClass()->GetName()),
            Property->GetClass()->GetName());
    }

    // -----------------------------------------------------------------------
    // Step 9: EncodeProperty (read-back) — symmetric to SetProperty.
    // -----------------------------------------------------------------------

    TSharedPtr<FJsonValue> EncodeProperty(
        FProperty* Property,
        const void* PropAddr,
        const FString& CurrentPath,
        int32 Depth)
    {
        if (!Property || !PropAddr)
        {
            return MakeShared<FJsonValueNull>();
        }

        // Depth guard — malformed reflection graphs could theoretically loop.
        if (Depth >= kMaxCoercionDepth)
        {
            return MakeShared<FJsonValueNull>();
        }

        if (FBoolProperty* P = CastField<FBoolProperty>(Property))
        {
            return MakeShared<FJsonValueBoolean>(P->GetPropertyValue(PropAddr));
        }
        if (FNumericProperty* P = CastField<FNumericProperty>(Property))
        {
            const double D = P->IsFloatingPoint()
                ? P->GetFloatingPointPropertyValue(PropAddr)
                : (double)P->GetSignedIntPropertyValue(PropAddr);
            return MakeShared<FJsonValueNumber>(D);
        }
        if (FStrProperty* P = CastField<FStrProperty>(Property))
        {
            return MakeShared<FJsonValueString>(P->GetPropertyValue(PropAddr));
        }
        if (FNameProperty* P = CastField<FNameProperty>(Property))
        {
            return MakeShared<FJsonValueString>(P->GetPropertyValue(PropAddr).ToString());
        }
        if (FTextProperty* P = CastField<FTextProperty>(Property))
        {
            return MakeShared<FJsonValueString>(P->GetPropertyValue(PropAddr).ToString());
        }
        if (FEnumProperty* P = CastField<FEnumProperty>(Property))
        {
            const int64 EnumVal = P->GetUnderlyingProperty()->GetSignedIntPropertyValue(PropAddr);
            return MakeShared<FJsonValueString>(P->GetEnum()->GetNameStringByValue(EnumVal));
        }

        // USTRUCT encode — v0.3.0 named-struct fast paths + generic fallback.
        if (FStructProperty* P = CastField<FStructProperty>(Property))
        {
            UScriptStruct* Struct = P->Struct;
            TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();

            if (Struct == TBaseStructure<FVector>::Get())
            {
                const FVector& V = *(const FVector*)PropAddr;
                Obj->SetNumberField(TEXT("x"), V.X);
                Obj->SetNumberField(TEXT("y"), V.Y);
                Obj->SetNumberField(TEXT("z"), V.Z);
                return MakeShared<FJsonValueObject>(Obj);
            }
            if (Struct == TBaseStructure<FVector2D>::Get())
            {
                const FVector2D& V = *(const FVector2D*)PropAddr;
                Obj->SetNumberField(TEXT("x"), V.X);
                Obj->SetNumberField(TEXT("y"), V.Y);
                return MakeShared<FJsonValueObject>(Obj);
            }
            if (Struct == TBaseStructure<FRotator>::Get())
            {
                const FRotator& R = *(const FRotator*)PropAddr;
                Obj->SetNumberField(TEXT("pitch"), R.Pitch);
                Obj->SetNumberField(TEXT("yaw"),   R.Yaw);
                Obj->SetNumberField(TEXT("roll"),  R.Roll);
                return MakeShared<FJsonValueObject>(Obj);
            }
            if (Struct == TBaseStructure<FLinearColor>::Get())
            {
                const FLinearColor& C = *(const FLinearColor*)PropAddr;
                Obj->SetNumberField(TEXT("r"), C.R);
                Obj->SetNumberField(TEXT("g"), C.G);
                Obj->SetNumberField(TEXT("b"), C.B);
                Obj->SetNumberField(TEXT("a"), C.A);
                return MakeShared<FJsonValueObject>(Obj);
            }
            if (Struct == TBaseStructure<FColor>::Get())
            {
                const FColor& C = *(const FColor*)PropAddr;
                Obj->SetNumberField(TEXT("r"), C.R);
                Obj->SetNumberField(TEXT("g"), C.G);
                Obj->SetNumberField(TEXT("b"), C.B);
                Obj->SetNumberField(TEXT("a"), C.A);
                return MakeShared<FJsonValueObject>(Obj);
            }

            // Generic USTRUCT: iterate all reflected fields.
            for (TFieldIterator<FProperty> It(Struct); It; ++It)
            {
                FProperty* FieldProp = *It;
                const void* FieldAddr = FieldProp->ContainerPtrToValuePtr<void>(PropAddr);
                const FString FieldPath = ChildPath(CurrentPath, FieldProp->GetName());
                TSharedPtr<FJsonValue> FieldVal = EncodeProperty(FieldProp, FieldAddr, FieldPath, Depth + 1);
                Obj->SetField(FieldProp->GetName(), FieldVal);
            }
            return MakeShared<FJsonValueObject>(Obj);
        }

        // TArray encode.
        if (FArrayProperty* ArrayProp = CastField<FArrayProperty>(Property))
        {
            TArray<TSharedPtr<FJsonValue>> Arr;
            FScriptArrayHelper Helper(ArrayProp, PropAddr);
            for (int32 i = 0; i < Helper.Num(); ++i)
            {
                const void* ElemAddr = Helper.GetRawPtr(i);
                const FString ElemPath = IndexedPath(CurrentPath, i);
                Arr.Add(EncodeProperty(ArrayProp->Inner, ElemAddr, ElemPath, Depth + 1));
            }
            return MakeShared<FJsonValueArray>(Arr);
        }

        // TSet encode (as JSON array).
        if (FSetProperty* SetProp = CastField<FSetProperty>(Property))
        {
            TArray<TSharedPtr<FJsonValue>> Arr;
            FScriptSetHelper Helper(SetProp, PropAddr);
            int32 LogicalIndex = 0;
            for (int32 i = 0; i < Helper.GetMaxIndex(); ++i)
            {
                if (!Helper.IsValidIndex(i)) continue;
                const void* ElemAddr = Helper.GetElementPtr(i);
                const FString ElemPath = IndexedPath(CurrentPath, LogicalIndex++);
                Arr.Add(EncodeProperty(SetProp->ElementProp, ElemAddr, ElemPath, Depth + 1));
            }
            return MakeShared<FJsonValueArray>(Arr);
        }

        // TMap encode (as JSON object — string/name keys become JSON string keys;
        // non-string keys fall back to ToString()).
        if (FMapProperty* MapProp = CastField<FMapProperty>(Property))
        {
            TSharedRef<FJsonObject> MapObj = MakeShared<FJsonObject>();
            FScriptMapHelper Helper(MapProp, PropAddr);
            for (int32 i = 0; i < Helper.GetMaxIndex(); ++i)
            {
                if (!Helper.IsValidIndex(i)) continue;
                const void* KeyAddr = Helper.GetKeyPtr(i);
                const void* ValAddr = Helper.GetValuePtr(i);

                FString KeyStr;
                if (FStrProperty* StrKey = CastField<FStrProperty>(MapProp->KeyProp))
                {
                    KeyStr = StrKey->GetPropertyValue(KeyAddr);
                }
                else if (FNameProperty* NameKey = CastField<FNameProperty>(MapProp->KeyProp))
                {
                    KeyStr = NameKey->GetPropertyValue(KeyAddr).ToString();
                }
                else
                {
                    // Non-string key: produce a best-effort string representation.
                    MapProp->KeyProp->ExportTextItem_Direct(KeyStr, KeyAddr, nullptr, nullptr, PPF_None);
                }

                const FString ValPath = ChildPath(CurrentPath, KeyStr);
                MapObj->SetField(KeyStr, EncodeProperty(MapProp->ValueProp, ValAddr, ValPath, Depth + 1));
            }
            return MakeShared<FJsonValueObject>(MapObj);
        }

        // FObjectProperty encode — return asset path string or null.
        if (FObjectProperty* ObjProp = CastField<FObjectProperty>(Property))
        {
            UObject* Obj = ObjProp->GetObjectPropertyValue(PropAddr);
            if (!Obj)
            {
                return MakeShared<FJsonValueNull>();
            }
            return MakeShared<FJsonValueString>(Obj->GetPathName());
        }

        // FSoftObjectPtr encode.
        if (FSoftObjectProperty* P = CastField<FSoftObjectProperty>(Property))
        {
            return MakeShared<FJsonValueString>(P->GetPropertyValue(PropAddr).ToString());
        }

        // Anything else — null (graceful fallback; caller gets null old_value/new_value).
        return MakeShared<FJsonValueNull>();
    }

    // -----------------------------------------------------------------------
    // Legacy compatibility wrappers (v0.3.0 public surface).
    // -----------------------------------------------------------------------

    FProperty* FindProperty(UObject* Target, const FString& PropertyName)
    {
        if (!Target) return nullptr;
        return Target->GetClass()->FindPropertyByName(FName(*PropertyName));
    }

    TSharedPtr<FJsonValue> GetProperty(UObject* Target, FProperty* Property)
    {
        if (!Target || !Property)
        {
            return MakeShared<FJsonValueNull>();
        }
        const void* PropAddr = Property->ContainerPtrToValuePtr<void>(Target);
        return EncodeProperty(Property, PropAddr, TEXT("."), 0);
    }
}
