// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_landscape - read structural properties from an ALandscape actor in
// the active editor world. Landscapes are scene actors, not content assets, so
// this handler intentionally uses TActorIterator instead of asset loading.
//
// UE 5.7 surface used:
//   - ALandscape                                  Landscape.h:276
//   - ALandscape::GetLoadedBounds                 Landscape.h:331
//   - ALandscapeProxy::ComponentSizeQuads         LandscapeProxy.h:898
//   - ALandscapeProxy::SubsectionSizeQuads        LandscapeProxy.h:901
//   - ALandscapeProxy::NumSubsections             LandscapeProxy.h:904
//   - ALandscapeProxy::GetLandscapeGuid           LandscapeProxy.h:1091
//   - ALandscapeProxy::GetOriginalLandscapeGuid   LandscapeProxy.h:1107
//   - ALandscapeProxy::GetLandscapeInfo           LandscapeProxy.h:1220
//   - ALandscapeProxy::GetLandscapeMaterial       LandscapeProxy.h:1286
//   - ULandscapeInfo::GetSortedStreamingProxies   LandscapeInfo.h:388
//   - ULandscapeInfo::ForEachLandscapeProxy       LandscapeInfo.h:398
//
// Error format: "inspect_landscape: <error_code>: <human-readable detail>"
// Stable error codes: no_editor_world, landscape_not_found,
// ambiguous_landscape.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Landscape.h"
#include "LandscapeInfo.h"
#include "LandscapeProxy.h"
#include "LandscapeStreamingProxy.h"
#include "Materials/MaterialInterface.h"

namespace
{
    static TSharedPtr<FJsonObject> VectorToJson(const FVector& V)
    {
        TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
        Obj->SetNumberField(TEXT("x"), V.X);
        Obj->SetNumberField(TEXT("y"), V.Y);
        Obj->SetNumberField(TEXT("z"), V.Z);
        return Obj;
    }

    static TSharedPtr<FJsonObject> BoxToJson(const FBox& Box)
    {
        // Mirror the bounds shape used by inspect_static_mesh and the
        // cleanup-updated inspect_niagara_system fixed_bounds: {min, max,
        // size, center}. Cross-handler consistency lets LLM consumers parse
        // bounds the same way regardless of which Inspect* they called.
        TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
        Obj->SetObjectField(TEXT("min"), VectorToJson(Box.Min));
        Obj->SetObjectField(TEXT("max"), VectorToJson(Box.Max));
        Obj->SetObjectField(TEXT("size"), VectorToJson(Box.GetSize()));
        Obj->SetObjectField(TEXT("center"), VectorToJson(Box.GetCenter()));
        return Obj;
    }
}

class FHandler_InspectLandscape : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_landscape"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        FString NameFilter;
        FString GuidFilter;
        if (Params.IsValid())
        {
            Params->TryGetStringField(TEXT("name"), NameFilter);
            Params->TryGetStringField(TEXT("guid"), GuidFilter);
        }

        if (!GEditor)
        {
            OutError = TEXT("inspect_landscape: no_editor_world: GEditor is null");
            return nullptr;
        }

        UWorld* World = GEditor->GetEditorWorldContext().World();
        if (!World)
        {
            OutError = TEXT("inspect_landscape: no_editor_world: World is null");
            return nullptr;
        }

        const bool bHasNameFilter = !NameFilter.IsEmpty();
        const bool bHasGuidFilter = !GuidFilter.IsEmpty();
        const bool bHasFilter = bHasNameFilter || bHasGuidFilter;

        TArray<ALandscape*> Matches;
        int32 TotalLandscapes = 0;
        for (TActorIterator<ALandscape> It(World); It; ++It)
        {
            ALandscape* Landscape = *It;
            if (!Landscape)
            {
                continue;
            }

            ++TotalLandscapes;

            if (bHasNameFilter && Landscape->GetActorLabel() != NameFilter)
            {
                continue;
            }

            if (bHasGuidFilter)
            {
                const FString LandscapeGuid = Landscape->GetLandscapeGuid().ToString();
                const FString OriginalLandscapeGuid = Landscape->GetOriginalLandscapeGuid().ToString();
                if (!LandscapeGuid.Equals(GuidFilter, ESearchCase::IgnoreCase)
                    && !OriginalLandscapeGuid.Equals(GuidFilter, ESearchCase::IgnoreCase))
                {
                    continue;
                }
            }

            Matches.Add(Landscape);
        }

        if (Matches.Num() == 0)
        {
            if (TotalLandscapes == 0)
            {
                OutError = TEXT("inspect_landscape: landscape_not_found: no ALandscape actors exist in the editor world");
            }
            else
            {
                OutError = FString::Printf(
                    TEXT("inspect_landscape: landscape_not_found: no ALandscape actor matched name='%s' guid='%s'"),
                    *NameFilter, *GuidFilter);
            }
            return nullptr;
        }

        if (!bHasFilter && Matches.Num() > 1)
        {
            OutError = FString::Printf(
                TEXT("inspect_landscape: ambiguous_landscape: found %d landscapes; provide 'name' or 'guid'"),
                Matches.Num());
            return nullptr;
        }

        ALandscape* Landscape = Matches[0];
        ULandscapeInfo* Info = Landscape->GetLandscapeInfo();
        const bool bHasLandscapeInfo = Info != nullptr;

        int32 StreamingProxyCount = 0;
        int32 ComponentCountTotal = 0;
        if (Info)
        {
            StreamingProxyCount = Info->GetSortedStreamingProxies().Num();
            Info->ForEachLandscapeProxy(
                [&ComponentCountTotal](ALandscapeProxy* Proxy) -> bool
                {
                    if (Proxy)
                    {
                        ComponentCountTotal += Proxy->LandscapeComponents.Num();
                    }
                    return true;
                });
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Landscape->GetActorLabel());
        Out->SetStringField(TEXT("actor_name"), Landscape->GetName());
        Out->SetStringField(TEXT("landscape_guid"), Landscape->GetLandscapeGuid().ToString());
        Out->SetStringField(TEXT("original_landscape_guid"), Landscape->GetOriginalLandscapeGuid().ToString());
        Out->SetNumberField(TEXT("component_size_quads"), static_cast<double>(Landscape->ComponentSizeQuads));
        Out->SetNumberField(TEXT("subsection_size_quads"), static_cast<double>(Landscape->SubsectionSizeQuads));
        Out->SetNumberField(TEXT("num_subsections"), static_cast<double>(Landscape->NumSubsections));

        if (UMaterialInterface* Material = Landscape->GetLandscapeMaterial())
        {
            Out->SetStringField(TEXT("landscape_material"), Material->GetPathName());
        }

        Out->SetObjectField(TEXT("loaded_bounds"), BoxToJson(Landscape->GetLoadedBounds()));
        Out->SetNumberField(TEXT("streaming_proxy_count"), static_cast<double>(StreamingProxyCount));
        Out->SetNumberField(TEXT("component_count_total"), static_cast<double>(ComponentCountTotal));
        Out->SetBoolField(TEXT("has_landscape_info"), bHasLandscapeInfo);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectLandscape()
{
    return MakeShared<FHandler_InspectLandscape>();
}
