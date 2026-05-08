// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/PropertyCoercion.h"

#include "Dom/JsonObject.h"
#include "UObject/UnrealType.h"
#include "UObject/EnumProperty.h"
#include "UObject/TextProperty.h"
#include "Math/Color.h"
#include "Math/Vector.h"
#include "Math/Vector2D.h"
#include "Math/Rotator.h"

namespace UCMCP::PropertyCoercion
{
    static FCoerceOutcome MakeOutcome(ECoerceResult R, const FString& Detail = FString(), const FString& Cls = FString())
    {
        return FCoerceOutcome{ R, Detail, Cls };
    }

    FProperty* FindProperty(UObject* Target, const FString& PropertyName)
    {
        if (!Target) return nullptr;
        return Target->GetClass()->FindPropertyByName(FName(*PropertyName));
    }

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

    FCoerceOutcome SetProperty(UObject* Target, FProperty* Property, const TSharedPtr<FJsonValue>& Value)
    {
        if (!Target || !Property || !Value.IsValid())
        {
            return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("null target/property/value"));
        }

        void* PropAddr = Property->ContainerPtrToValuePtr<void>(Target);

        // Bool
        if (FBoolProperty* BoolProp = CastField<FBoolProperty>(Property))
        {
            bool B;
            if (!Value->TryGetBool(B))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected bool"));
            }
            BoolProp->SetPropertyValue(PropAddr, B);
            return MakeOutcome(ECoerceResult::Success);
        }

        // Numeric (int*, uint*, float, double)
        if (FNumericProperty* NumProp = CastField<FNumericProperty>(Property))
        {
            double D;
            if (!Value->TryGetNumber(D))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected number"));
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

        // FString
        if (FStrProperty* StrProp = CastField<FStrProperty>(Property))
        {
            FString S;
            if (!Value->TryGetString(S))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected string"));
            }
            StrProp->SetPropertyValue(PropAddr, S);
            return MakeOutcome(ECoerceResult::Success);
        }

        // FName
        if (FNameProperty* NameProp = CastField<FNameProperty>(Property))
        {
            FString S;
            if (!Value->TryGetString(S))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected string"));
            }
            NameProp->SetPropertyValue(PropAddr, FName(*S));
            return MakeOutcome(ECoerceResult::Success);
        }

        // FText
        if (FTextProperty* TextProp = CastField<FTextProperty>(Property))
        {
            FString S;
            if (!Value->TryGetString(S))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected string"));
            }
            TextProp->SetPropertyValue(PropAddr, FText::FromString(S));
            return MakeOutcome(ECoerceResult::Success);
        }

        // Enum
        if (FEnumProperty* EnumProp = CastField<FEnumProperty>(Property))
        {
            FString S;
            if (!Value->TryGetString(S))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected enum value name string"));
            }
            UEnum* Enum = EnumProp->GetEnum();
            const int64 EnumVal = Enum->GetValueByNameString(S);
            if (EnumVal == INDEX_NONE)
            {
                return MakeOutcome(ECoerceResult::OutOfRange,
                    FString::Printf(TEXT("enum '%s' has no value named '%s'"),
                        *Enum->GetName(), *S));
            }
            EnumProp->GetUnderlyingProperty()->SetIntPropertyValue(PropAddr, EnumVal);
            return MakeOutcome(ECoerceResult::Success);
        }

        // Struct (FVector / FRotator / FLinearColor / FColor / FVector2D)
        if (FStructProperty* StructProp = CastField<FStructProperty>(Property))
        {
            UScriptStruct* Struct = StructProp->Struct;
            const TSharedPtr<FJsonObject>* ObjPtr = nullptr;
            if (!Value->TryGetObject(ObjPtr))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected JSON object for struct"));
            }
            const TSharedPtr<FJsonObject>& Obj = *ObjPtr;

            if (Struct == TBaseStructure<FVector>::Get())
            {
                FVector V; if (!ReadVector(Obj, V)) return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected {x,y,z}"));
                *(FVector*)PropAddr = V;
                return MakeOutcome(ECoerceResult::Success);
            }
            if (Struct == TBaseStructure<FVector2D>::Get())
            {
                FVector2D V; if (!ReadVector2D(Obj, V)) return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected {x,y}"));
                *(FVector2D*)PropAddr = V;
                return MakeOutcome(ECoerceResult::Success);
            }
            if (Struct == TBaseStructure<FRotator>::Get())
            {
                FRotator R; if (!ReadRotator(Obj, R)) return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected {pitch,yaw,roll}"));
                *(FRotator*)PropAddr = R;
                return MakeOutcome(ECoerceResult::Success);
            }
            if (Struct == TBaseStructure<FLinearColor>::Get())
            {
                FLinearColor C; if (!ReadLinearColor(Obj, C)) return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected {r,g,b,a}"));
                *(FLinearColor*)PropAddr = C;
                return MakeOutcome(ECoerceResult::Success);
            }
            if (Struct == TBaseStructure<FColor>::Get())
            {
                FColor C; if (!ReadColor(Obj, C)) return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected {r,g,b,a}"));
                *(FColor*)PropAddr = C;
                return MakeOutcome(ECoerceResult::Success);
            }

            // Custom structs deferred to v0.4.0
            return MakeOutcome(ECoerceResult::Unsupported,
                FString::Printf(TEXT("custom USTRUCT '%s' deferred to v0.4.0"), *Struct->GetName()),
                TEXT("StructProperty"));
        }

        // TSoftObjectPtr<T>
        if (FSoftObjectProperty* SoftProp = CastField<FSoftObjectProperty>(Property))
        {
            FString S;
            if (!Value->TryGetString(S))
            {
                return MakeOutcome(ECoerceResult::TypeMismatch, TEXT("expected asset path string"));
            }
            FSoftObjectPath Path(S);
            SoftProp->SetPropertyValue(PropAddr, Path);
            return MakeOutcome(ECoerceResult::Success);
        }

        // Anything else (TArray, TMap, TSet, FObjectProperty, FInstancedStruct, etc.)
        return MakeOutcome(ECoerceResult::Unsupported,
            FString::Printf(TEXT("FProperty class '%s' not in v0.3.0 supported list"),
                *Property->GetClass()->GetName()),
            Property->GetClass()->GetName());
    }

    TSharedPtr<FJsonValue> GetProperty(UObject* Target, FProperty* Property)
    {
        if (!Target || !Property)
        {
            return MakeShared<FJsonValueNull>();
        }
        const void* PropAddr = Property->ContainerPtrToValuePtr<void>(Target);

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
            if (Struct == TBaseStructure<FRotator>::Get())
            {
                const FRotator& R = *(const FRotator*)PropAddr;
                Obj->SetNumberField(TEXT("pitch"), R.Pitch);
                Obj->SetNumberField(TEXT("yaw"), R.Yaw);
                Obj->SetNumberField(TEXT("roll"), R.Roll);
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
            // Other structs: fall through to null (caller's old_value will just be null)
        }
        if (FSoftObjectProperty* P = CastField<FSoftObjectProperty>(Property))
        {
            return MakeShared<FJsonValueString>(P->GetPropertyValue(PropAddr).ToString());
        }
        return MakeShared<FJsonValueNull>();
    }
}
