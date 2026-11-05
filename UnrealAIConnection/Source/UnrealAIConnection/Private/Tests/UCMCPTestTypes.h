// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// Reflected fixture types for the UCMCP.* automation tests (Private/Tests/).
// They give PropertyCoercion one field of every supported FProperty kind
// without depending on engine classes whose members can change between
// releases. UHT does not allow these inside #if WITH_DEV_AUTOMATION_TESTS,
// so they always compile; they are tiny, Transient, and editor-module only.

#pragma once

#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "UObject/SoftObjectPtr.h"

#include "UCMCPTestTypes.generated.h"

UENUM()
enum class EUCMCPTestEnum : uint8
{
    Alpha,
    Beta,
    Gamma,
};

USTRUCT()
struct FUCMCPTestInner
{
    GENERATED_BODY()

    UPROPERTY()
    int32 Count = 0;

    UPROPERTY()
    FString Label;
};

USTRUCT()
struct FUCMCPTestStruct
{
    GENERATED_BODY()

    UPROPERTY()
    bool bFlag = false;

    UPROPERTY()
    int32 IntValue = 0;

    UPROPERTY()
    int64 Int64Value = 0;

    UPROPERTY()
    float FloatValue = 0.0f;

    UPROPERTY()
    double DoubleValue = 0.0;

    UPROPERTY()
    FString StrValue;

    UPROPERTY()
    FName NameValue;

    UPROPERTY()
    FText TextValue;

    UPROPERTY()
    EUCMCPTestEnum EnumValue = EUCMCPTestEnum::Alpha;

    UPROPERTY()
    FVector VectorValue = FVector::ZeroVector;

    UPROPERTY()
    FVector2D Vector2DValue = FVector2D::ZeroVector;

    UPROPERTY()
    FRotator RotatorValue = FRotator::ZeroRotator;

    UPROPERTY()
    FLinearColor LinearColorValue = FLinearColor::Black;

    UPROPERTY()
    FColor ColorValue = FColor::Black;

    UPROPERTY()
    FUCMCPTestInner Inner;

    UPROPERTY()
    TArray<int32> IntArray;

    UPROPERTY()
    TSet<FString> StringSet;

    UPROPERTY()
    TMap<FString, int32> StringIntMap;

    // Numeric-key TMap: PropertyCoercion reports these as Unsupported.
    UPROPERTY()
    TMap<int32, int32> IntKeyMap;

    UPROPERTY()
    TSoftObjectPtr<UObject> SoftRef;
};

UCLASS(Transient)
class UUCMCPTestObject : public UObject
{
    GENERATED_BODY()

public:
    UPROPERTY()
    FUCMCPTestStruct Data;

    // Hard object reference: exercises FObjectProperty coercion and the
    // ResolvePropertyPath hop into a second UObject (OwningObject).
    UPROPERTY()
    TObjectPtr<UUCMCPTestObject> Child = nullptr;
};
