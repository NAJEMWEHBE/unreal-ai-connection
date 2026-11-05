// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// UE Automation tests for MCP/PropertyCoercion (audit F20). Pure reflection
// work on the fixture types in UCMCPTestTypes.h: no world, no assets, no RHI.
// Run headless: see docs/HANDOFF.md, "Automation tests".

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "MCP/PropertyCoercion.h"
#include "Tests/UCMCPTestTypes.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"

namespace UCMCPPropertyCoercionTests
{
    using namespace UCMCP::PropertyCoercion;

    static TSharedPtr<FJsonValue> JNum(double V)       { return MakeShared<FJsonValueNumber>(V); }
    static TSharedPtr<FJsonValue> JBool(bool V)        { return MakeShared<FJsonValueBoolean>(V); }
    static TSharedPtr<FJsonValue> JStr(const FString& V) { return MakeShared<FJsonValueString>(V); }
    static TSharedPtr<FJsonValue> JNull()              { return MakeShared<FJsonValueNull>(); }
    static TSharedPtr<FJsonValue> JObj(const TSharedRef<FJsonObject>& O) { return MakeShared<FJsonValueObject>(O); }
    static TSharedPtr<FJsonValue> JArr(const TArray<TSharedPtr<FJsonValue>>& A) { return MakeShared<FJsonValueArray>(A); }

    static FProperty* FixtureField(const TCHAR* Name)
    {
        return FUCMCPTestStruct::StaticStruct()->FindPropertyByName(FName(Name));
    }

    /** SetProperty on one field of a fixture struct (no owning UObject). */
    static FCoerceOutcome SetFixture(FUCMCPTestStruct& Data, const TCHAR* FieldName, const TSharedPtr<FJsonValue>& Value)
    {
        FProperty* Prop = FixtureField(FieldName);
        if (!Prop)
        {
            return FCoerceOutcome{ ECoerceResult::Invalid, FString::Printf(TEXT("fixture has no field %s"), FieldName) };
        }
        return SetProperty(nullptr, Prop, Prop->ContainerPtrToValuePtr<void>(&Data), Value);
    }

    /** EncodeProperty on one field of a fixture struct. */
    static TSharedPtr<FJsonValue> EncodeFixture(const FUCMCPTestStruct& Data, const TCHAR* FieldName)
    {
        FProperty* Prop = FixtureField(FieldName);
        if (!Prop)
        {
            return nullptr;
        }
        return EncodeProperty(Prop, Prop->ContainerPtrToValuePtr<void>(&Data));
    }

    static bool IsResult(const FCoerceOutcome& Outcome, ECoerceResult Expected)
    {
        return Outcome.Result == Expected;
    }
}

// ---------------------------------------------------------------------------
// Scalars: bool, numbers, strings, FName, FText, enum
// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUCMCPPropertyCoercionScalarsTest,
    "UCMCP.PropertyCoercion.Scalars",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUCMCPPropertyCoercionScalarsTest::RunTest(const FString& Parameters)
{
    using namespace UCMCPPropertyCoercionTests;
    FUCMCPTestStruct Data;

    TestTrue(TEXT("bool: set true"), IsResult(SetFixture(Data, TEXT("bFlag"), JBool(true)), ECoerceResult::Success));
    TestTrue(TEXT("bool: value"), Data.bFlag);

    TestTrue(TEXT("int32: set 42"), IsResult(SetFixture(Data, TEXT("IntValue"), JNum(42.0)), ECoerceResult::Success));
    TestEqual(TEXT("int32: value"), Data.IntValue, 42);
    TestTrue(TEXT("int32: set -7.9 truncates"), IsResult(SetFixture(Data, TEXT("IntValue"), JNum(-7.9)), ECoerceResult::Success));
    TestEqual(TEXT("int32: truncated value"), Data.IntValue, -7);

    TestTrue(TEXT("int64: set 2^40"), IsResult(SetFixture(Data, TEXT("Int64Value"), JNum(1099511627776.0)), ECoerceResult::Success));
    TestEqual(TEXT("int64: value"), Data.Int64Value, static_cast<int64>(1099511627776LL));

    TestTrue(TEXT("float: set 1.5"), IsResult(SetFixture(Data, TEXT("FloatValue"), JNum(1.5)), ECoerceResult::Success));
    TestEqual(TEXT("float: value"), Data.FloatValue, 1.5f);

    TestTrue(TEXT("double: set 0.125"), IsResult(SetFixture(Data, TEXT("DoubleValue"), JNum(0.125)), ECoerceResult::Success));
    TestEqual(TEXT("double: value"), Data.DoubleValue, 0.125);

    TestTrue(TEXT("FString: set"), IsResult(SetFixture(Data, TEXT("StrValue"), JStr(TEXT("hello"))), ECoerceResult::Success));
    TestEqual(TEXT("FString: value"), Data.StrValue, FString(TEXT("hello")));

    TestTrue(TEXT("FName: set"), IsResult(SetFixture(Data, TEXT("NameValue"), JStr(TEXT("SomeName"))), ECoerceResult::Success));
    TestEqual(TEXT("FName: value"), Data.NameValue, FName(TEXT("SomeName")));

    TestTrue(TEXT("FText: set"), IsResult(SetFixture(Data, TEXT("TextValue"), JStr(TEXT("Shown text"))), ECoerceResult::Success));
    TestEqual(TEXT("FText: value"), Data.TextValue.ToString(), FString(TEXT("Shown text")));

    TestTrue(TEXT("enum: set by short name"), IsResult(SetFixture(Data, TEXT("EnumValue"), JStr(TEXT("Beta"))), ECoerceResult::Success));
    TestTrue(TEXT("enum: value"), Data.EnumValue == EUCMCPTestEnum::Beta);
    TestTrue(TEXT("enum: unknown name is OutOfRange"),
        IsResult(SetFixture(Data, TEXT("EnumValue"), JStr(TEXT("Delta"))), ECoerceResult::OutOfRange));
    TestTrue(TEXT("enum: value unchanged after OutOfRange"), Data.EnumValue == EUCMCPTestEnum::Beta);

    // Mismatches. FJsonValue converts freely between number/bool/string, so
    // use null / object / array, which never convert to a scalar.
    TestTrue(TEXT("bool from null: TypeMismatch"), IsResult(SetFixture(Data, TEXT("bFlag"), JNull()), ECoerceResult::TypeMismatch));
    TestTrue(TEXT("int from object: TypeMismatch"),
        IsResult(SetFixture(Data, TEXT("IntValue"), JObj(MakeShared<FJsonObject>())), ECoerceResult::TypeMismatch));
    TestTrue(TEXT("string from array: TypeMismatch"),
        IsResult(SetFixture(Data, TEXT("StrValue"), JArr({})), ECoerceResult::TypeMismatch));
    TestTrue(TEXT("enum from null: TypeMismatch"), IsResult(SetFixture(Data, TEXT("EnumValue"), JNull()), ECoerceResult::TypeMismatch));

    // Encode round-trip.
    double Num = 0.0;
    TSharedPtr<FJsonValue> IntJson = EncodeFixture(Data, TEXT("IntValue"));
    TestTrue(TEXT("encode int32 is a number"), IntJson.IsValid() && IntJson->TryGetNumber(Num));
    TestEqual(TEXT("encode int32 value"), Num, -7.0);

    bool B = false;
    TSharedPtr<FJsonValue> BoolJson = EncodeFixture(Data, TEXT("bFlag"));
    TestTrue(TEXT("encode bool"), BoolJson.IsValid() && BoolJson->Type == EJson::Boolean && BoolJson->TryGetBool(B) && B);

    FString S;
    TSharedPtr<FJsonValue> EnumJson = EncodeFixture(Data, TEXT("EnumValue"));
    TestTrue(TEXT("encode enum is a string"), EnumJson.IsValid() && EnumJson->Type == EJson::String && EnumJson->TryGetString(S));
    TestEqual(TEXT("encode enum uses the short name"), S, FString(TEXT("Beta")));

    TSharedPtr<FJsonValue> NameJson = EncodeFixture(Data, TEXT("NameValue"));
    TestTrue(TEXT("encode FName"), NameJson.IsValid() && NameJson->TryGetString(S) && S == TEXT("SomeName"));

    return true;
}

// ---------------------------------------------------------------------------
// Structs: named fast paths + generic USTRUCT walk
// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUCMCPPropertyCoercionStructsTest,
    "UCMCP.PropertyCoercion.Structs",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUCMCPPropertyCoercionStructsTest::RunTest(const FString& Parameters)
{
    using namespace UCMCPPropertyCoercionTests;
    FUCMCPTestStruct Data;

    {
        TSharedRef<FJsonObject> V = MakeShared<FJsonObject>();
        V->SetNumberField(TEXT("x"), 1.0);
        V->SetNumberField(TEXT("y"), 2.0);
        V->SetNumberField(TEXT("z"), 3.0);
        TestTrue(TEXT("FVector: set"), IsResult(SetFixture(Data, TEXT("VectorValue"), JObj(V)), ECoerceResult::Success));
        TestEqual(TEXT("FVector: value"), Data.VectorValue, FVector(1.0, 2.0, 3.0));

        const TSharedPtr<FJsonValue> Enc = EncodeFixture(Data, TEXT("VectorValue"));
        const TSharedPtr<FJsonObject>* EncObj = nullptr;
        double Z = 0.0;
        TestTrue(TEXT("FVector: encodes {x,y,z}"),
            Enc.IsValid() && Enc->TryGetObject(EncObj) && (*EncObj)->TryGetNumberField(TEXT("z"), Z));
        TestEqual(TEXT("FVector: encoded z"), Z, 3.0);

        TSharedRef<FJsonObject> Missing = MakeShared<FJsonObject>();
        Missing->SetNumberField(TEXT("x"), 1.0);
        Missing->SetNumberField(TEXT("y"), 2.0);
        TestTrue(TEXT("FVector: missing z is TypeMismatch"),
            IsResult(SetFixture(Data, TEXT("VectorValue"), JObj(Missing)), ECoerceResult::TypeMismatch));
        TestTrue(TEXT("FVector: number instead of object is TypeMismatch"),
            IsResult(SetFixture(Data, TEXT("VectorValue"), JNum(1.0)), ECoerceResult::TypeMismatch));
    }

    {
        TSharedRef<FJsonObject> V = MakeShared<FJsonObject>();
        V->SetNumberField(TEXT("x"), 4.0);
        V->SetNumberField(TEXT("y"), 5.0);
        TestTrue(TEXT("FVector2D: set"), IsResult(SetFixture(Data, TEXT("Vector2DValue"), JObj(V)), ECoerceResult::Success));
        TestEqual(TEXT("FVector2D: x"), Data.Vector2DValue.X, 4.0);
        TestEqual(TEXT("FVector2D: y"), Data.Vector2DValue.Y, 5.0);
    }

    {
        TSharedRef<FJsonObject> R = MakeShared<FJsonObject>();
        R->SetNumberField(TEXT("pitch"), 10.0);
        R->SetNumberField(TEXT("yaw"), 20.0);
        R->SetNumberField(TEXT("roll"), 30.0);
        TestTrue(TEXT("FRotator: set"), IsResult(SetFixture(Data, TEXT("RotatorValue"), JObj(R)), ECoerceResult::Success));
        TestEqual(TEXT("FRotator: value"), Data.RotatorValue, FRotator(10.0, 20.0, 30.0));
    }

    {
        TSharedRef<FJsonObject> C = MakeShared<FJsonObject>();
        C->SetNumberField(TEXT("r"), 0.25);
        C->SetNumberField(TEXT("g"), 0.5);
        C->SetNumberField(TEXT("b"), 0.75);
        TestTrue(TEXT("FLinearColor: set without a"), IsResult(SetFixture(Data, TEXT("LinearColorValue"), JObj(C)), ECoerceResult::Success));
        TestEqual(TEXT("FLinearColor: alpha defaults to 1"), Data.LinearColorValue, FLinearColor(0.25f, 0.5f, 0.75f, 1.0f));
    }

    {
        TSharedRef<FJsonObject> C = MakeShared<FJsonObject>();
        C->SetNumberField(TEXT("r"), 300.0);   // clamps to 255
        C->SetNumberField(TEXT("g"), -5.0);    // clamps to 0
        C->SetNumberField(TEXT("b"), 128.0);
        TestTrue(TEXT("FColor: set without a"), IsResult(SetFixture(Data, TEXT("ColorValue"), JObj(C)), ECoerceResult::Success));
        TestEqual(TEXT("FColor: clamped, alpha defaults to 255"), Data.ColorValue, FColor(255, 0, 128, 255));
    }

    {
        TSharedRef<FJsonObject> Inner = MakeShared<FJsonObject>();
        Inner->SetNumberField(TEXT("Count"), 3.0);
        Inner->SetStringField(TEXT("Label"), TEXT("three"));
        TestTrue(TEXT("generic USTRUCT: set"), IsResult(SetFixture(Data, TEXT("Inner"), JObj(Inner)), ECoerceResult::Success));
        TestEqual(TEXT("generic USTRUCT: Count"), Data.Inner.Count, 3);
        TestEqual(TEXT("generic USTRUCT: Label"), Data.Inner.Label, FString(TEXT("three")));

        const TSharedPtr<FJsonValue> Enc = EncodeFixture(Data, TEXT("Inner"));
        const TSharedPtr<FJsonObject>* EncObj = nullptr;
        FString Label;
        TestTrue(TEXT("generic USTRUCT: encodes every field by name"),
            Enc.IsValid() && Enc->TryGetObject(EncObj)
            && (*EncObj)->HasField(TEXT("Count"))
            && (*EncObj)->TryGetStringField(TEXT("Label"), Label));
        TestEqual(TEXT("generic USTRUCT: encoded Label"), Label, FString(TEXT("three")));

        TSharedRef<FJsonObject> Unknown = MakeShared<FJsonObject>();
        Unknown->SetNumberField(TEXT("Nope"), 1.0);
        const FCoerceOutcome UnknownOutcome = SetFixture(Data, TEXT("Inner"), JObj(Unknown));
        TestTrue(TEXT("generic USTRUCT: unknown field is PropertyNotFound"),
            IsResult(UnknownOutcome, ECoerceResult::PropertyNotFound));
        TestTrue(TEXT("generic USTRUCT: detail names the field"), UnknownOutcome.Detail.Contains(TEXT("Nope")));

        TSharedRef<FJsonObject> BadField = MakeShared<FJsonObject>();
        BadField->SetField(TEXT("Count"), JNull());
        const FCoerceOutcome BadOutcome = SetFixture(Data, TEXT("Inner"), JObj(BadField));
        TestTrue(TEXT("generic USTRUCT: bad field type bubbles up TypeMismatch"),
            IsResult(BadOutcome, ECoerceResult::TypeMismatch));
        TestTrue(TEXT("generic USTRUCT: detail has the field path"), BadOutcome.Detail.Contains(TEXT(".Count")));
    }

    return true;
}

// ---------------------------------------------------------------------------
// Containers: TArray, TSet, TMap
// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUCMCPPropertyCoercionContainersTest,
    "UCMCP.PropertyCoercion.Containers",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUCMCPPropertyCoercionContainersTest::RunTest(const FString& Parameters)
{
    using namespace UCMCPPropertyCoercionTests;
    FUCMCPTestStruct Data;

    // TArray<int32>
    TestTrue(TEXT("TArray: set [1,2,3]"),
        IsResult(SetFixture(Data, TEXT("IntArray"), JArr({ JNum(1.0), JNum(2.0), JNum(3.0) })), ECoerceResult::Success));
    TestEqual(TEXT("TArray: Num"), Data.IntArray.Num(), 3);
    if (Data.IntArray.Num() == 3)
    {
        TestEqual(TEXT("TArray: [2]"), Data.IntArray[2], 3);
    }

    TestTrue(TEXT("TArray: replaces, not appends"),
        IsResult(SetFixture(Data, TEXT("IntArray"), JArr({ JNum(9.0) })), ECoerceResult::Success));
    TestEqual(TEXT("TArray: Num after replace"), Data.IntArray.Num(), 1);

    const FCoerceOutcome BadElem = SetFixture(Data, TEXT("IntArray"), JArr({ JNum(1.0), JNull() }));
    TestTrue(TEXT("TArray: bad element is TypeMismatch"), IsResult(BadElem, ECoerceResult::TypeMismatch));
    TestTrue(TEXT("TArray: detail has the element index"), BadElem.Detail.Contains(TEXT("[1]")));
    TestTrue(TEXT("TArray: object instead of array is TypeMismatch"),
        IsResult(SetFixture(Data, TEXT("IntArray"), JObj(MakeShared<FJsonObject>())), ECoerceResult::TypeMismatch));

    TSharedPtr<FJsonValue> ArrJson = EncodeFixture(Data, TEXT("IntArray"));
    const TArray<TSharedPtr<FJsonValue>>* EncArr = nullptr;
    TestTrue(TEXT("TArray: encodes as array"), ArrJson.IsValid() && ArrJson->TryGetArray(EncArr));

    // TSet<FString> (distinct elements; see PR notes on duplicates)
    TestTrue(TEXT("TSet: set [a,b]"),
        IsResult(SetFixture(Data, TEXT("StringSet"), JArr({ JStr(TEXT("a")), JStr(TEXT("b")) })), ECoerceResult::Success));
    TestEqual(TEXT("TSet: Num"), Data.StringSet.Num(), 2);
    TestTrue(TEXT("TSet: lookup works after rehash"), Data.StringSet.Contains(TEXT("b")));
    TSharedPtr<FJsonValue> SetJson = EncodeFixture(Data, TEXT("StringSet"));
    const TArray<TSharedPtr<FJsonValue>>* EncSet = nullptr;
    TestTrue(TEXT("TSet: encodes as 2-element array"),
        SetJson.IsValid() && SetJson->TryGetArray(EncSet) && EncSet->Num() == 2);

    const FCoerceOutcome BadSet = SetFixture(Data, TEXT("StringSet"), JArr({ JStr(TEXT("c")), JNull() }));
    TestTrue(TEXT("TSet: bad element is TypeMismatch"), IsResult(BadSet, ECoerceResult::TypeMismatch));
    TestEqual(TEXT("TSet: left empty (and valid) after a failure"), Data.StringSet.Num(), 0);

    // TMap<FString, int32>
    {
        TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
        M->SetNumberField(TEXT("one"), 1.0);
        M->SetNumberField(TEXT("two"), 2.0);
        TestTrue(TEXT("TMap<FString,int32>: set"), IsResult(SetFixture(Data, TEXT("StringIntMap"), JObj(M)), ECoerceResult::Success));
        TestEqual(TEXT("TMap: Num"), Data.StringIntMap.Num(), 2);
        TestEqual(TEXT("TMap: lookup works after rehash"), Data.StringIntMap.FindRef(TEXT("two")), 2);

        TSharedPtr<FJsonValue> MapJson = EncodeFixture(Data, TEXT("StringIntMap"));
        const TSharedPtr<FJsonObject>* EncMap = nullptr;
        double One = 0.0;
        TestTrue(TEXT("TMap: encodes as object"),
            MapJson.IsValid() && MapJson->TryGetObject(EncMap) && (*EncMap)->TryGetNumberField(TEXT("one"), One));
        TestEqual(TEXT("TMap: encoded value"), One, 1.0);

        TSharedRef<FJsonObject> Bad = MakeShared<FJsonObject>();
        Bad->SetField(TEXT("x"), JNull());
        TestTrue(TEXT("TMap: bad value is TypeMismatch"), IsResult(SetFixture(Data, TEXT("StringIntMap"), JObj(Bad)), ECoerceResult::TypeMismatch));
        TestEqual(TEXT("TMap: left empty (and valid) after a failure"), Data.StringIntMap.Num(), 0);
    }

    // TMap<int32, int32>: unsupported key type.
    {
        TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
        M->SetNumberField(TEXT("1"), 1.0);
        const FCoerceOutcome Outcome = SetFixture(Data, TEXT("IntKeyMap"), JObj(M));
        TestTrue(TEXT("TMap<int32,..>: Unsupported"), IsResult(Outcome, ECoerceResult::Unsupported));
        TestTrue(TEXT("TMap<int32,..>: detail says non-string key"), Outcome.Detail.Contains(TEXT("non-string key")));
    }

    return true;
}

// ---------------------------------------------------------------------------
// Object references: FObjectProperty + TSoftObjectPtr
// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUCMCPPropertyCoercionObjectRefsTest,
    "UCMCP.PropertyCoercion.ObjectRefs",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUCMCPPropertyCoercionObjectRefsTest::RunTest(const FString& Parameters)
{
    using namespace UCMCPPropertyCoercionTests;

    UUCMCPTestObject* Root = NewObject<UUCMCPTestObject>(GetTransientPackage());
    UUCMCPTestObject* Other = NewObject<UUCMCPTestObject>(GetTransientPackage());
    if (!TestNotNull(TEXT("fixture objects"), Root) || !TestNotNull(TEXT("fixture objects"), Other))
    {
        return false;
    }
    FProperty* ChildProp = UUCMCPTestObject::StaticClass()->FindPropertyByName(FName(TEXT("Child")));
    if (!TestNotNull(TEXT("Child property"), ChildProp))
    {
        return false;
    }
    void* ChildAddr = ChildProp->ContainerPtrToValuePtr<void>(Root);

    // In-memory object by path (LoadObject finds loaded objects first).
    TestTrue(TEXT("FObjectProperty: set by path"),
        IsResult(SetProperty(Root, ChildProp, ChildAddr, JStr(Other->GetPathName())), ECoerceResult::Success));
    TestTrue(TEXT("FObjectProperty: points at the object"), Root->Child == Other);

    TSharedPtr<FJsonValue> Enc = EncodeProperty(ChildProp, ChildAddr);
    FString EncPath;
    TestTrue(TEXT("FObjectProperty: encodes as the path"), Enc.IsValid() && Enc->TryGetString(EncPath));
    TestEqual(TEXT("FObjectProperty: encoded path"), EncPath, Other->GetPathName());

    // Wrong class: the fixture's UClass is a loaded UObject, but not a UUCMCPTestObject.
    const FString WrongClassPath = UUCMCPTestObject::StaticClass()->GetPathName();
    const FCoerceOutcome Wrong = SetProperty(Root, ChildProp, ChildAddr, JStr(WrongClassPath));
    TestTrue(TEXT("FObjectProperty: wrong class is TypeMismatch"), IsResult(Wrong, ECoerceResult::TypeMismatch));
    TestTrue(TEXT("FObjectProperty: detail says wrong_object_class"), Wrong.Detail.Contains(TEXT("wrong_object_class")));
    TestTrue(TEXT("FObjectProperty: unchanged after the failure"), Root->Child == Other);

    // Empty string clears.
    TestTrue(TEXT("FObjectProperty: empty string clears"),
        IsResult(SetProperty(Root, ChildProp, ChildAddr, JStr(FString())), ECoerceResult::Success));
    TestTrue(TEXT("FObjectProperty: now null"), Root->Child == nullptr);
    TSharedPtr<FJsonValue> NullEnc = EncodeProperty(ChildProp, ChildAddr);
    TestTrue(TEXT("FObjectProperty: null encodes as JSON null"), NullEnc.IsValid() && NullEnc->Type == EJson::Null);

    // Soft reference: stored as a path, nothing is loaded.
    FUCMCPTestStruct Data;
    const FString SoftPath = TEXT("/Game/UCMCPTest/NotThere.NotThere");
    TestTrue(TEXT("TSoftObjectPtr: set by path"), IsResult(SetFixture(Data, TEXT("SoftRef"), JStr(SoftPath)), ECoerceResult::Success));
    TestEqual(TEXT("TSoftObjectPtr: stored path"), Data.SoftRef.ToSoftObjectPath().ToString(), SoftPath);
    TSharedPtr<FJsonValue> SoftEnc = EncodeFixture(Data, TEXT("SoftRef"));
    FString SoftEncPath;
    TestTrue(TEXT("TSoftObjectPtr: encodes as the path"), SoftEnc.IsValid() && SoftEnc->TryGetString(SoftEncPath));
    TestEqual(TEXT("TSoftObjectPtr: encoded path"), SoftEncPath, SoftPath);

    return true;
}

// ---------------------------------------------------------------------------
// ResolvePropertyPath
// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUCMCPPropertyCoercionPathTest,
    "UCMCP.PropertyCoercion.ResolvePropertyPath",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUCMCPPropertyCoercionPathTest::RunTest(const FString& Parameters)
{
    using namespace UCMCPPropertyCoercionTests;

    UUCMCPTestObject* Root = NewObject<UUCMCPTestObject>(GetTransientPackage());
    UUCMCPTestObject* Child = NewObject<UUCMCPTestObject>(GetTransientPackage());
    if (!TestNotNull(TEXT("fixture objects"), Root) || !TestNotNull(TEXT("fixture objects"), Child))
    {
        return false;
    }

    // Through two structs on the root object.
    {
        FResolvedProperty Resolved;
        const FCoerceOutcome Outcome = ResolvePropertyPath(Root, TEXT("Data.Inner.Count"), Resolved);
        TestTrue(TEXT("struct path: Success"), IsResult(Outcome, ECoerceResult::Success));
        TestTrue(TEXT("struct path: property is Count"), Resolved.Property && Resolved.Property->GetFName() == FName(TEXT("Count")));
        TestTrue(TEXT("struct path: address is the real field"), Resolved.PropAddr == &Root->Data.Inner.Count);
        TestTrue(TEXT("struct path: container is the inner struct"), Resolved.Container == &Root->Data.Inner);
        TestTrue(TEXT("struct path: owning object is the root"), Resolved.OwningObject == Root);
        TestEqual(TEXT("struct path: resolved path"), Resolved.ResolvedPath, FString(TEXT("Data.Inner.Count")));

        // Write through the resolved address.
        TestTrue(TEXT("struct path: SetProperty through it"),
            IsResult(SetProperty(Resolved.OwningObject, Resolved.Property, Resolved.PropAddr, JNum(11.0)), ECoerceResult::Success));
        TestEqual(TEXT("struct path: value landed"), Root->Data.Inner.Count, 11);
    }

    // Null object hop.
    {
        FResolvedProperty Resolved;
        const FCoerceOutcome Outcome = ResolvePropertyPath(Root, TEXT("Child.Data.IntValue"), Resolved);
        TestTrue(TEXT("null hop: TypeMismatch"), IsResult(Outcome, ECoerceResult::TypeMismatch));
        TestTrue(TEXT("null hop: detail says path_traversal_null"), Outcome.Detail.Contains(TEXT("path_traversal_null")));
    }

    // Object hop: OwningObject moves to the child.
    Root->Child = Child;
    {
        FResolvedProperty Resolved;
        const FCoerceOutcome Outcome = ResolvePropertyPath(Root, TEXT("Child.Data.IntValue"), Resolved);
        TestTrue(TEXT("object hop: Success"), IsResult(Outcome, ECoerceResult::Success));
        TestTrue(TEXT("object hop: address is the child's field"), Resolved.PropAddr == &Child->Data.IntValue);
        TestTrue(TEXT("object hop: owning object is the child"), Resolved.OwningObject == Child);
    }

    // Errors.
    {
        FResolvedProperty Resolved;
        TestTrue(TEXT("missing segment: PropertyNotFound"),
            IsResult(ResolvePropertyPath(Root, TEXT("Data.Nope"), Resolved), ECoerceResult::PropertyNotFound));
        TestTrue(TEXT("through a scalar: Unsupported"),
            IsResult(ResolvePropertyPath(Root, TEXT("Data.IntValue.X"), Resolved), ECoerceResult::Unsupported));
        TestTrue(TEXT("empty path: Invalid"),
            IsResult(ResolvePropertyPath(Root, FString(), Resolved), ECoerceResult::Invalid));
        TestTrue(TEXT("only dots: Invalid"),
            IsResult(ResolvePropertyPath(Root, TEXT("..."), Resolved), ECoerceResult::Invalid));
        TestTrue(TEXT("null root: TypeMismatch"),
            IsResult(ResolvePropertyPath(nullptr, TEXT("Data"), Resolved), ECoerceResult::TypeMismatch));
    }

    // Legacy helpers.
    TestNotNull(TEXT("FindProperty: Data"), FindProperty(Root, TEXT("Data")));
    TestNull(TEXT("FindProperty: missing"), FindProperty(Root, TEXT("Nope")));
    TestNull(TEXT("FindProperty: null target"), FindProperty(nullptr, TEXT("Data")));
    TSharedPtr<FJsonValue> Legacy = GetProperty(Root, FindProperty(Root, TEXT("Data")));
    TestTrue(TEXT("GetProperty: struct encodes as object"), Legacy.IsValid() && Legacy->Type == EJson::Object);

    return true;
}

// ---------------------------------------------------------------------------
// Guards: depth limit, null inputs, unsupported encode
// ---------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUCMCPPropertyCoercionGuardsTest,
    "UCMCP.PropertyCoercion.Guards",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUCMCPPropertyCoercionGuardsTest::RunTest(const FString& Parameters)
{
    using namespace UCMCPPropertyCoercionTests;
    FUCMCPTestStruct Data;
    FProperty* IntProp = FixtureField(TEXT("IntValue"));
    if (!TestNotNull(TEXT("IntValue property"), IntProp))
    {
        return false;
    }
    void* IntAddr = IntProp->ContainerPtrToValuePtr<void>(&Data);

    const FCoerceOutcome Deep = SetProperty(nullptr, IntProp, IntAddr, JNum(1.0), TEXT("."), kMaxCoercionDepth);
    TestTrue(TEXT("depth limit: Invalid"), IsResult(Deep, ECoerceResult::Invalid));
    TestTrue(TEXT("depth limit: detail says recursion_depth_exceeded"), Deep.Detail.Contains(TEXT("recursion_depth_exceeded")));
    TestEqual(TEXT("depth limit: value untouched"), Data.IntValue, 0);

    TestTrue(TEXT("null property: TypeMismatch"),
        IsResult(SetProperty(nullptr, nullptr, IntAddr, JNum(1.0)), ECoerceResult::TypeMismatch));
    TestTrue(TEXT("null value: TypeMismatch"),
        IsResult(SetProperty(nullptr, IntProp, IntAddr, TSharedPtr<FJsonValue>()), ECoerceResult::TypeMismatch));
    TestTrue(TEXT("legacy 3-arg SetProperty with null target: TypeMismatch"),
        IsResult(SetProperty(static_cast<UObject*>(nullptr), IntProp, JNum(1.0)), ECoerceResult::TypeMismatch));

    TSharedPtr<FJsonValue> NullEnc = EncodeProperty(nullptr, IntAddr);
    TestTrue(TEXT("encode null property: JSON null"), NullEnc.IsValid() && NullEnc->Type == EJson::Null);
    TSharedPtr<FJsonValue> DeepEnc = EncodeProperty(IntProp, IntAddr, TEXT("."), kMaxCoercionDepth);
    TestTrue(TEXT("encode past depth limit: JSON null"), DeepEnc.IsValid() && DeepEnc->Type == EJson::Null);

    return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
