// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_curve - read structural properties of a UCurveBase asset
// (UCurveFloat / UCurveLinearColor / UCurveVector / any UCurveBase
// subclass): per-channel name, key count, and per-channel time/value
// ranges, plus the global time and value range across all channels.
//
// Channel layout per concrete subclass (cited in the header comment
// block below):
//   UCurveFloat        -> 1 channel  ("FloatCurve")
//   UCurveLinearColor  -> 4 channels (R, G, B, A)
//   UCurveVector       -> 3 channels (X, Y, Z)
//
// UE 5.7 surface used:
//   CurveBase.h:17       class UCurveBase
//   CurveBase.h:25       GetTimeRange(float&, float&) const
//   CurveBase.h:30       GetValueRange(float&, float&) const
//   CurveOwnerInterface  TArray<FRichCurveEditInfo> GetCurves()
//   RichCurve.h:469      struct FRichCurveEditInfoTemplate<T>
//   RichCurve.h:472      FName CurveName
//   RichCurve.h:475      T    CurveToEdit (FRealCurve* on the non-const typedef)
//   RichCurve.h:198      struct FRichCurve : FRealCurve
//   RichCurve.h:299      FRichCurve::GetTimeRange (final override)
//   RichCurve.h:302      FRichCurve::GetValueRange (final override)
//   RichCurve.h:356      TArray<FRichCurveKey> Keys
//
// Key counting strategy: GetCurves() returns FRealCurve*. The concrete
// curve carried by every UCurveBase subclass we care about is FRichCurve
// (UCurveFloat::FloatCurve, UCurveLinearColor::FloatCurves[4],
// UCurveVector::FloatCurves[3] are all FRichCurve). Cast FRealCurve* ->
// FRichCurve* to read Keys.Num(); when the cast fails (theoretical
// future subclass using FSimpleCurve etc.) emit key_count: -1 so callers
// can disambiguate "zero keys" from "key count not knowable".
//
// Error format: "inspect_curve: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_a_curve

#include "Curves/CurveBase.h"
#include "Curves/RichCurve.h"
#include "Curves/RealCurve.h"
#include "EditorAssetLibrary.h"
#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

namespace
{
    static TSharedRef<FJsonObject> MakeRangeObject(float MinV, float MaxV)
    {
        TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
        Obj->SetNumberField(TEXT("min"), static_cast<double>(MinV));
        Obj->SetNumberField(TEXT("max"), static_cast<double>(MaxV));
        return Obj;
    }
}

class FHandler_InspectCurve : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_curve"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_curve: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_curve: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);

        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("inspect_curve: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }

        UCurveBase* Curve = Cast<UCurveBase>(Loaded);
        if (!Curve)
        {
            OutError = FString::Printf(
                TEXT("inspect_curve: not_a_curve: '%s' is a %s, not a UCurveBase"),
                *InputPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }

        // Global time + value range across all channels.
        float GlobalTimeMin  = 0.f, GlobalTimeMax  = 0.f;
        float GlobalValueMin = 0.f, GlobalValueMax = 0.f;
        Curve->GetTimeRange(GlobalTimeMin, GlobalTimeMax);
        Curve->GetValueRange(GlobalValueMin, GlobalValueMax);

        // Per-channel info from the curve owner interface.
        TArray<FRichCurveEditInfo> Channels = Curve->GetCurves();
        TArray<TSharedPtr<FJsonValue>> ChannelArray;
        ChannelArray.Reserve(Channels.Num());

        for (const FRichCurveEditInfo& Info : Channels)
        {
            TSharedPtr<FJsonObject> ChObj = MakeShared<FJsonObject>();
            ChObj->SetStringField(TEXT("name"), Info.CurveName.ToString());

            // Key-count strategy: every UCurveBase subclass we care about
            // (UCurveFloat, UCurveLinearColor, UCurveVector) stores its curves
            // as FRichCurve. The FRealCurve* typedef in FRichCurveEditInfo is
            // a generalisation; in practice the carrier is always FRichCurve.
            // Cast directly to read Keys.Num(); when CurveToEdit is null
            // (defensive guard) emit key_count: -1 so callers can disambiguate
            // "zero keys" from "not knowable".
            const FRealCurve* RealCurve = Info.CurveToEdit;
            if (RealCurve)
            {
                const FRichCurve* RichCurve = static_cast<const FRichCurve*>(RealCurve);
                ChObj->SetNumberField(TEXT("key_count"), static_cast<double>(RichCurve->Keys.Num()));
            }
            else
            {
                ChObj->SetNumberField(TEXT("key_count"), -1.0);
            }

            // Per-channel range via the polymorphic FRealCurve accessors.
            if (RealCurve)
            {
                float ChTimeMin = 0.f, ChTimeMax = 0.f, ChValueMin = 0.f, ChValueMax = 0.f;
                RealCurve->GetTimeRange(ChTimeMin, ChTimeMax);
                RealCurve->GetValueRange(ChValueMin, ChValueMax);
                ChObj->SetObjectField(TEXT("time_range"),  MakeRangeObject(ChTimeMin,  ChTimeMax));
                ChObj->SetObjectField(TEXT("value_range"), MakeRangeObject(ChValueMin, ChValueMax));
            }

            ChannelArray.Add(MakeShared<FJsonValueObject>(ChObj));
        }

        // --- response ---

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Curve->GetName());
        Out->SetStringField(TEXT("path"), ObjectPath);
        Out->SetStringField(TEXT("curve_class"), Curve->GetClass()->GetName());
        Out->SetNumberField(TEXT("channel_count"), static_cast<double>(Channels.Num()));
        Out->SetObjectField(TEXT("time_range"),  MakeRangeObject(GlobalTimeMin,  GlobalTimeMax));
        Out->SetObjectField(TEXT("value_range"), MakeRangeObject(GlobalValueMin, GlobalValueMax));
        Out->SetArrayField(TEXT("channels"),    ChannelArray);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectCurve()
{
    return MakeShared<FHandler_InspectCurve>();
}
